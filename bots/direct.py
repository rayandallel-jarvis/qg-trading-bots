"""Bot en direct sur le compte démo (paper) Alpaca.

Tourne en boucle (une vérification par minute) pendant la durée demandée, puis s'arrête ;
GitHub Actions le relance. L'état (trades, cycle, garde-fous) est enregistré dans etat/etat.json
et poussé sur GitHub pour que le Trésorier et le QG puissent le lire.
"""
import argparse
import os
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest, MarketOrderRequest

from .commun import (RACINE, Garde, charger_config, ecrire_json, lire_json, notifier, resultat_r,
                     statistiques, taille)
from .donnees import Donnees
from .marche import Marche
from .strategies import NOMS, STRATEGIES

PREFIXE = "qg"
ARRET = False


def _arret(*_):
    global ARRET
    ARRET = True


def maintenant():
    return datetime.now(timezone.utc)


def heure_paris():
    t = pd.Timestamp(maintenant()).tz_convert("Europe/Paris")
    return t.hour * 60 + t.minute, str(t.date())


def statut(o):
    st = getattr(o.status, "value", o.status)
    return str(st).lower()


def symbole_position(s):
    return s.replace("/", "")


class Bot:
    def __init__(self):
        self.cfg = charger_config()
        self.d = Donnees()
        self.api = TradingClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True)
        self.etat = lire_json("etat.json", {})
        self.etat.setdefault("cycle", {"numero": 1, "debut": maintenant().isoformat(timespec="seconds"),
                                       "quota": self.cfg["cycle"]["trades_par_cycle"], "trades": 0})
        self.etat.setdefault("en_cours", [])
        self.etat.setdefault("historique", [])
        self.etat.setdefault("garde", {})
        self.garde = Garde(self.cfg, self.etat["garde"])
        self.barres = {}
        self.derniere = {}
        self.modifie = True
        self.dernier_push = time.time()

    # -------------------------------------------------------------- utilitaires
    def actif(self, s):
        return self.cfg["actifs"][s]

    def capital(self):
        return float(self.api.get_account().equity)

    def maj_barres(self, s):
        a = self.actif(s)
        if s not in self.barres or self.barres[s].empty:
            self.barres[s] = self.d.historique(s, a["type"], 6)
        else:
            debut = self.barres[s].index[-1] - pd.Timedelta(minutes=5)
            neuf = self.d.barres(s, a["type"], debut.to_pydatetime())
            df = pd.concat([self.barres[s], neuf])
            df = df[~df.index.duplicated(keep="last")].sort_index()
            self.barres[s] = df[df.index >= df.index[-1] - pd.Timedelta(days=6)]
        return self.barres[s]

    def arrondi(self, s, prix):
        return round(prix, 2)

    def ordre(self, s, sens, qte, type_ordre, prix=None, cid=None):
        a = self.actif(s)
        tif = TimeInForce.GTC if a["type"] == "crypto" else TimeInForce.DAY
        cote = OrderSide.BUY if sens == 1 else OrderSide.SELL
        if type_ordre == "limite":
            req = LimitOrderRequest(symbol=s, qty=qte, side=cote, time_in_force=tif,
                                    limit_price=self.arrondi(s, prix), client_order_id=cid)
        else:
            req = MarketOrderRequest(symbol=s, qty=qte, side=cote, time_in_force=tif, client_order_id=cid)
        return self.api.submit_order(req)

    def attendre_execution(self, oid, essais=10):
        o = None
        for _ in range(essais):
            o = self.api.get_order_by_id(oid)
            if statut(o) == "filled" and o.filled_avg_price:
                return o
            time.sleep(1.5)
        return o

    # -------------------------------------------------------------- démarrage
    def reconcilier(self):
        """Ferme ce qui traîne chez Alpaca sans être suivi dans l'état (ex. arrêt brutal)."""
        suivis = {t["actif_position"] for t in self.etat["en_cours"]}
        for o in self.api.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)):
            if (o.client_order_id or "").startswith(PREFIXE) and not any(
                    t.get("ordre_id") == str(o.id) for t in self.etat["en_cours"]):
                self.api.cancel_order_by_id(o.id)
        for p in self.api.get_all_positions():
            if p.symbol not in suivis:
                self.api.close_position(p.symbol)
                notifier(f"Position {p.symbol} non suivie fermée au démarrage.", tags="warning")

    # -------------------------------------------------------------- gestion des trades
    def suivre(self, t, m):
        s, a = t["actif"], self.actif(t["actif"])
        pm, _ = heure_paris()
        cloture = self.cfg["risque"]["cloture_actions"]
        fin_actions = a["type"] == "action" and pm >= int(cloture[:2]) * 60 + int(cloture[3:])

        if t["statut"] == "attente":
            o = self.api.get_order_by_id(t["ordre_id"])
            rempli = float(o.filled_qty or 0)
            expire = maintenant() >= datetime.fromisoformat(t["expire"]) or fin_actions
            if statut(o) == "filled":
                self.ouvrir(t, float(o.filled_avg_price), rempli)
            elif expire:
                self.api.cancel_order_by_id(t["ordre_id"])
                if rempli > 0:
                    self.ouvrir(t, float(o.filled_avg_price), rempli)
                else:
                    t["statut"] = "annule"
            return

        if t["statut"] == "ouvert":
            depuis = pd.Timestamp(t["verifie"])
            nouv = m.df[m.df.index >= depuis]
            motif = None
            for _, b in nouv.iterrows():
                if (t["sens"] == 1 and b.low <= t["stop"]) or (t["sens"] == -1 and b.high >= t["stop"]):
                    motif = "stop"
                    break
                if (t["sens"] == 1 and b.high >= t["cible"]) or (t["sens"] == -1 and b.low <= t["cible"]):
                    motif = "objectif"
                    break
            if not nouv.empty:
                t["verifie"] = str(nouv.index[-1] + pd.Timedelta(minutes=1))
            if motif is None and fin_actions:
                motif = "cloture"
            if motif:
                self.fermer(t, motif)

    def ouvrir(self, t, prix, qte):
        t.update({"statut": "ouvert", "entree_reelle": prix, "qte": qte,
                  "ouvert_le": maintenant().isoformat(timespec="seconds"),
                  "verifie": str(pd.Timestamp(maintenant()).floor("min"))})
        self.garde.ouvert(t["strategie"], t["actif"], t["jour"])
        sens = "Achat" if t["sens"] == 1 else "Vente"
        notifier(f"{NOMS[t['strategie']]} · {sens} {t['actif']} à {prix:.2f}\n"
                 f"Stop {t['stop']:.2f} · Objectif {t['cible']:.2f} ({t['r_prevu']}R prévu)", tags="chart_with_upwards_trend")
        self.modifie = True

    def fermer(self, t, motif):
        o = self.api.close_position(t["actif_position"])
        o = self.attendre_execution(o.id)
        sortie = float(o.filled_avg_price) if o and o.filled_avg_price else (
            t["stop"] if motif == "stop" else t["cible"])
        r = resultat_r(t["sens"], t["entree_reelle"], sortie, t["stop"], self.actif(t["actif"]))
        t.update({"statut": "ferme", "sortie": sortie, "motif": motif, "r": r,
                  "ferme_le": maintenant().isoformat(timespec="seconds")})
        self.garde.ferme(t["strategie"], t["jour"], r)
        self.etat["cycle"]["trades"] += 1
        icone = "white_check_mark" if r > 0 else "x"
        notifier(f"{NOMS[t['strategie']]} · {t['actif']} fermé ({motif}) : {r:+.2f}R\n"
                 f"Cycle : {self.etat['cycle']['trades']}/{self.etat['cycle']['quota']} trades", tags=icone)
        if self.etat["cycle"]["trades"] >= self.etat["cycle"]["quota"]:
            notifier("Cycle terminé : le bilan part au Trésorier.", tags="trophy")
        self.modifie = True

    def chercher(self, s, m):
        a = self.actif(s)
        i = m.n - 1
        pm, jour = heure_paris()
        cloture = self.cfg["risque"]["cloture_actions"]
        if a["type"] == "action" and pm >= int(cloture[:2]) * 60 + int(cloture[3:]) - 10:
            return
        if self.cfg["risque"]["une_position_par_actif"] and any(
                t["actif"] == s for t in self.etat["en_cours"]):
            return
        for nom, p in self.cfg["strategies"].items():
            if not p.get("actif", True) or not self.garde.autorise(nom, s, jour):
                continue
            sig = STRATEGIES[nom](m, i, p)
            if not sig or (sig["sens"] == -1 and not a.get("vente_a_decouvert", True)):
                continue
            capital = self.capital()
            qte, risque, refus = taille(sig, capital, a, self.cfg)
            if refus:
                print(f"{nom} {s} : signal ignoré ({refus})")
                continue
            cid = f"{PREFIXE}-{nom}-{symbole_position(s)}-{int(time.time())}"
            o = self.ordre(s, sig["sens"], qte, sig["ordre"], sig["entree"], cid)
            minutes = sig["expiration"] if sig["ordre"] == "limite" else 3
            t = {**sig, "strategie": nom, "actif": s, "actif_position": symbole_position(s), "jour": jour,
                 "statut": "attente", "ordre_id": str(o.id), "qte_demandee": qte, "risque_usd": round(risque, 2),
                 "signal_le": maintenant().isoformat(timespec="seconds"),
                 "expire": (maintenant() + timedelta(minutes=minutes)).isoformat(timespec="seconds")}
            self.etat["en_cours"].append(t)
            self.modifie = True
            print("Nouvel ordre", t)
            return

    # -------------------------------------------------------------- boucle
    def tour(self):
        for s in self.cfg["actifs"]:
            try:
                df = self.maj_barres(s)
                if len(df) < 300 or self.derniere.get(s) == df.index[-1]:
                    continue
                self.derniere[s] = df.index[-1]
                a = self.actif(s)
                m = Marche(df, a["type"], self.cfg["swing_k"], self.cfg["strategies"]["vp"]["zone_valeur"])
                for t in [t for t in self.etat["en_cours"] if t["actif"] == s]:
                    self.suivre(t, m)
                self.chercher(s, m)
            except Exception as exc:
                print(f"Erreur sur {s} :", repr(exc))
        termines = [t for t in self.etat["en_cours"] if t["statut"] in ("ferme", "annule")]
        if termines:
            self.etat["historique"] = (self.etat["historique"] +
                                       [t for t in termines if t["statut"] == "ferme"])[-500:]
            self.etat["en_cours"] = [t for t in self.etat["en_cours"] if t not in termines]
            self.modifie = True

    def sauver(self, pousser=False):
        hist = self.etat["historique"]
        debut_cycle = self.etat["cycle"]["debut"]
        cycle = [t for t in hist if t.get("ferme_le", "") >= debut_cycle]
        self.etat["stats_cycle"] = {
            nom: statistiques([t["r"] for t in cycle if t["strategie"] == nom]) for nom in self.cfg["strategies"]}
        self.etat["stats_cycle"]["global"] = statistiques([t["r"] for t in cycle])
        try:
            self.etat["capital"] = round(self.capital(), 2)
        except Exception:
            pass
        self.etat["maj"] = maintenant().isoformat(timespec="seconds")
        self.etat["garde"] = self.garde.e
        ecrire_json("etat.json", self.etat)
        self.modifie = False
        if pousser:
            git_pousser()

    def lancer(self, minutes):
        fin = time.time() + minutes * 60
        self.reconcilier()
        notifier(f"Bots démarrés ({', '.join(self.cfg['actifs'])}). "
                 f"Cycle {self.etat['cycle']['numero']} : {self.etat['cycle']['trades']}/"
                 f"{self.etat['cycle']['quota']} trades.", tags="rocket")
        while time.time() < fin and not ARRET:
            t0 = time.time()
            _, jour = heure_paris()
            self.garde.nettoyer(jour)
            self.tour()
            if self.modifie:
                self.sauver(pousser=time.time() - self.dernier_push > 600)
                if time.time() - self.dernier_push > 600:
                    self.dernier_push = time.time()
            # attendre la minute suivante (+5 s pour laisser Alpaca publier la bougie)
            pause = 65 - (time.time() % 60)
            while pause > 0 and not ARRET:
                time.sleep(min(pause, 5))
                pause -= 5
            if time.time() - t0 > 600:
                print("Tour anormalement long")
        self.sauver(pousser=True)


def git_pousser():
    cmds = [["git", "add", "etat"],
            ["git", "commit", "-q", "-m", "État des bots"],
            ["git", "pull", "-q", "--rebase", "-X", "theirs"],
            ["git", "push", "-q"]]
    for c in cmds:
        r = subprocess.run(c, cwd=RACINE, capture_output=True, text=True)
        if r.returncode and c[1] != "commit":
            print("git :", " ".join(c), r.stderr[-300:])
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=340)
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, _arret)
    signal.signal(signal.SIGINT, _arret)
    Bot().lancer(args.minutes)


if __name__ == "__main__":
    main()
