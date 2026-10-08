"""Bot en direct sur le compte démo IG (CFD) : Nasdaq 100, or, EUR/USD, Bitcoin.

- Bougies 1 min : historique Dukascopy (jours terminés) + bougies IG gardées dans etat/barres_ig/
  + bougies du jour construites en direct à partir des prix IG (instantanés toutes les 5 s,
  milieu achat/vente). Un trou court au redémarrage est comblé par l'historique IG (quota limité).
- Entrées : un ordre limite des stratégies est surveillé par le bot et exécuté au marché quand
  le prix l'atteint (même règle que le backtest). Stop et objectif sont posés chez IG avec l'ordre.
- Séparation : seules les positions dont la référence commence par « QGB » sont gérées ;
  les positions prises à la main par Rayan ne sont jamais touchées.
- `--essai` : se connecte, construit les bougies, cherche les signaux et calcule les tailles,
  mais ne passe aucun ordre.
"""
import argparse
import gzip
import math
import re
import signal
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .backtest import construire_marche
from .commun import (ETAT, Garde, charger_config, ecrire_json, lire_json, notifier, resultat_r,
                     statistiques, stop_min, taille)
from .direct import git_pousser
from .donnees_duka import historique as historique_duka
from .ig import PREFIXE_REF, IG, ErreurIG
from . import strategies as S
from .strategies import NOMS, fonction

ARRET = False
DOSSIER_BARRES = ETAT / "barres_ig"
COLS = ["open", "high", "low", "close", "volume"]


def _arret(*_):
    global ARRET
    ARRET = True


def maintenant():
    return datetime.now(timezone.utc)


def heure_paris(t=None):
    t = pd.Timestamp(t or maintenant()).tz_convert("Europe/Paris")
    return t.hour * 60 + t.minute, str(t.date()), t.dayofweek


def _nombre(txt, defaut=1.0):
    m = re.search(r"[0-9]*\.?[0-9]+", str(txt or ""))
    return float(m.group()) if m else defaut


class Actif:
    """Paramètres d'un marché IG, convertis à l'échelle des cours IG (ex. EUR/USD coté 11200,5)."""

    def __init__(self, nom, a, ig):
        self.nom, self.a, self.epic = nom, a, a["epic"]
        d = ig.get(f"/markets/{self.epic}", version="3")
        ins, snap, r = d.get("instrument", {}), d.get("snapshot", {}), d.get("dealingRules", {})
        self.devise = (ins.get("currencies") or [{}])[0].get("code") or "USD"
        self.valeur_pip = float(ins.get("valueOfOnePip") or 1)
        self.pip = _nombre(ins.get("onePipMeans"), 1.0)
        self.taille_min = float((r.get("minDealSize") or {}).get("value") or 0.01)
        self.bid0 = snap.get("bid")
        self.echelle = 1.0
        self.decimales = int(snap.get("decimalPlacesFactor") or 2)

    def regler_echelle(self, prix_source):
        """Rapport entre le cours IG et le cours Dukascopy (1 ou une puissance de 10)."""
        if self.bid0 and prix_source:
            self.echelle = float(10 ** round(math.log10(self.bid0 / prix_source)))
        # paramètres de coût et de stop exprimés en cours IG
        self.ap = dict(self.a)
        for k in ("ecart_coeur", "ecart_hors", "glissement", "stop_min"):
            if k in self.ap:
                self.ap[k] = self.ap[k] * self.echelle
        # valeur d'une unité de cours IG, en devise du contrat
        self.valeur_unite = self.valeur_pip * (1 / self.echelle) / self.pip if self.echelle != 1 else (
            self.valeur_pip / self.pip)

    def arrondi(self, prix):
        return round(prix, self.decimales)


class Bot:
    def __init__(self, essai=False):
        self.essai = essai
        self.cfg = charger_config()
        self.ig = IG()
        self.ig.connecter()
        self.actifs = {n: a for n, a in self.cfg["actifs"].items() if a.get("actif", True) and a.get("broker") == "ig"}
        self.marches = {n: Actif(n, a, self.ig) for n, a in self.actifs.items()}
        self.etat = lire_json("etat_ig.json", {})
        self.etat.setdefault("cycle", {"numero": 1, "debut": maintenant().isoformat(timespec="seconds"),
                                       "quota": self.cfg["cycle"]["trades_par_cycle"], "trades": 0})
        for k, v in (("en_cours", []), ("historique", []), ("garde", {}), ("ecarts", {})):
            self.etat.setdefault(k, v)
        self.etat["broker"] = "IG démo"
        self.garde = Garde(self.cfg, self.etat["garde"])
        self.barres = {}
        self.courante = {}          # bougie en cours par actif : [debut, o, h, l, c, n]
        self.derniere = {}
        self.modifie = True
        self.dernier_push = 0

    # -------------------------------------------------------------- bougies
    def _fichier(self, n):
        return DOSSIER_BARRES / f"{n}.csv.gz"

    def _lire_gardees(self, n):
        f = self._fichier(n)
        if not f.exists():
            return pd.DataFrame(columns=COLS)
        df = pd.read_csv(f, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True)
        return df[COLS].astype(float)

    def garder_barres(self):
        DOSSIER_BARRES.mkdir(parents=True, exist_ok=True)
        for n, df in self.barres.items():
            recent = df[df.index >= df.index[-1] - pd.Timedelta(days=3)] if len(df) else df
            with gzip.open(self._fichier(n), "wt") as g:
                recent.round(6).to_csv(g)

    def _prix_ig(self, actif, debut, fin):
        prix = self.ig.prix_historiques(actif.epic, debut, fin)
        lignes = []
        for p in prix:
            def mid(k):
                v = p.get(k) or {}
                b, a = v.get("bid"), v.get("ask")
                return (b + a) / 2 if b is not None and a is not None else (b if b is not None else a)
            t = pd.Timestamp(p.get("snapshotTimeUTC"), tz="UTC")
            lignes.append((t, mid("openPrice"), mid("highPrice"), mid("lowPrice"), mid("closePrice"),
                           float(p.get("lastTradedVolume") or 0)))
        df = pd.DataFrame(lignes, columns=["t"] + COLS).set_index("t").dropna()
        return df / [actif.echelle, actif.echelle, actif.echelle, actif.echelle, 1] if len(df) else df

    def initialiser_barres(self):
        """Historique de départ, à l'échelle Dukascopy (convertie ensuite en cours IG)."""
        for n, actif in self.marches.items():
            duka = historique_duka(actif.a["instrument"], 20)
            actif.regler_echelle(float(duka["close"].iloc[-1]) if len(duka) else None)
            gardees = self._lire_gardees(n)
            if len(gardees):
                gardees = gardees.copy()
                gardees[["open", "high", "low", "close"]] /= actif.echelle
            df = pd.concat([duka, gardees]).sort_index()
            df = df[~df.index.duplicated(keep="last")]
            # combler le trou jusqu'à maintenant avec l'historique IG (quota de 10 000 bougies par semaine) :
            # au plus 26 h au premier lancement, 6 h ensuite (les bougies IG sont gardées entre deux passages)
            limite = timedelta(hours=6 if len(gardees) else 26)
            debut = df.index[-1] + pd.Timedelta(minutes=1) if len(df) else maintenant() - limite
            debut = max(pd.Timestamp(debut), pd.Timestamp(maintenant() - limite))
            fin = pd.Timestamp(maintenant()).floor("min") - pd.Timedelta(minutes=1)
            if fin > debut:
                try:
                    trou = self._prix_ig(actif, debut.tz_convert(None).to_pydatetime(),
                                         fin.tz_convert(None).to_pydatetime())
                    df = pd.concat([df, trou]).sort_index()
                    df = df[~df.index.duplicated(keep="last")]
                    print(f"{n} : {len(trou)} bougies IG pour combler le trou")
                    self.etat.setdefault("demarrages", []).append(
                        {"t": maintenant().isoformat(timespec="seconds"), "actif": n, "bougies_ig": len(trou)})
                    self.etat["demarrages"] = self.etat["demarrages"][-20:]
                except ErreurIG as e:
                    self.erreur(f"{n} : trou non comblé ({e})")
            # tout passe à l'échelle IG
            df = df.copy()
            df[["open", "high", "low", "close"]] *= actif.echelle
            self.barres[n] = df[df.index >= df.index[-1] - pd.Timedelta(days=20)]
            print(f"{n} ({actif.epic}) : {len(self.barres[n])} bougies, échelle {actif.echelle:g}, "
                  f"dernière {self.barres[n].index[-1]}")

    def echantillonner(self):
        """Instantané des prix IG ; met à jour la bougie 1 min en cours et clôt les bougies terminées."""
        snap = self.ig.marches([a.epic for a in self.marches.values()])
        t = pd.Timestamp(maintenant()).floor("min")
        for n, actif in self.marches.items():
            s = snap.get(actif.epic) or {}
            if s.get("statut") != "TRADEABLE" or s.get("bid") is None or s.get("offer") is None:
                continue
            mid = (s["bid"] + s["offer"]) / 2
            self._noter_ecart(n, s["offer"] - s["bid"])
            c = self.courante.get(n)
            if c and c[0] < t:
                self._clore(n, c)
                c = None
            if not c:
                self.courante[n] = [t, mid, mid, mid, mid, 1]
            else:
                c[2], c[3], c[4], c[5] = max(c[2], mid), min(c[3], mid), mid, c[5] + 1

    def clore_bougies_passees(self):
        t = pd.Timestamp(maintenant()).floor("min")
        for n, c in list(self.courante.items()):
            if c and c[0] < t:
                self._clore(n, c)
                self.courante[n] = None

    def _clore(self, n, c):
        ligne = pd.DataFrame([c[1:]], columns=COLS, index=pd.DatetimeIndex([c[0]]))
        df = pd.concat([self.barres[n], ligne])
        self.barres[n] = df[~df.index.duplicated(keep="last")].sort_index()

    def _noter_ecart(self, n, e):
        pm, _, _ = heure_paris()
        cle = f"{n}|{pm // 60:02d}h"
        d = self.etat["ecarts"].setdefault(cle, {"n": 0, "moy": 0.0, "max": 0.0})
        d["n"] += 1
        d["moy"] = round(d["moy"] + (e - d["moy"]) / d["n"], 6)
        d["max"] = round(max(d["max"], e), 6)

    # -------------------------------------------------------------- compte
    def capital(self):
        return self.ig.solde()

    def taux_conversion(self, devise):
        """Unités de la devise du contrat pour 1 € (le compte est en euros)."""
        if devise == "EUR":
            return 1.0
        if devise == "USD":
            eur = next((a for a in self.marches.values() if a.nom == "EURUSD"), None)
            if eur is not None and len(self.barres.get("EURUSD", [])):
                return float(self.barres["EURUSD"]["close"].iloc[-1]) / eur.echelle
            try:
                m = self.ig.marche("CS.D.EURUSD.CEF.IP")
                v = (m["bid"] + m["offer"]) / 2
                return v / 10000 if v > 100 else v
            except Exception:
                return 1.10
        return 1.0

    def taille_ig(self, actif, sig, capital_eur):
        """Taille IG (contrats) pour risquer le % prévu, et refus éventuel."""
        qte, _, refus = taille(sig, capital_eur, actif.ap, self.cfg, heure_paris()[0])
        if refus:
            return 0, refus
        risque_eur = capital_eur * self.cfg["risque"]["risque_par_trade_pct"] / 100
        risque_ccy = risque_eur * self.taux_conversion(actif.devise)
        distance = abs(sig["entree"] - sig["stop"])
        contrats = risque_ccy / (distance * actif.valeur_unite)
        plafond = capital_eur * self.taux_conversion(actif.devise) * actif.ap.get("notionnel_max_pct", 100) / 100
        contrats = min(contrats, plafond / (sig["entree"] * actif.valeur_unite))
        contrats = math.floor(contrats * 100) / 100
        if contrats < actif.taille_min:
            return 0, "taille inférieure au minimum IG"
        return contrats, None

    # -------------------------------------------------------------- trades
    def chercher(self, n, m):
        actif = self.marches[n]
        i = m.n - 1
        pm, jour, jsem = heure_paris()
        if self._avant_fermeture(actif, pm, jsem, marge=30):
            return
        if self.cfg["risque"]["une_position_par_actif"] and any(t["actif"] == n for t in self.etat["en_cours"]):
            return
        for nom, p in self.cfg["strategies"].items():
            if not p.get("actif", True) or not self.garde.autorise(nom, n, jour):
                continue
            S.COURANT = nom
            sig = fonction(nom, p)(m, i, p)
            if not sig:
                continue
            capital = self.capital()
            contrats, refus = self.taille_ig(actif, sig, capital)
            if refus:
                print(f"{nom} {n} : signal ignoré ({refus})")
                continue
            minutes = sig["expiration"] if sig["ordre"] == "limite" else 2
            t = {**{k: v for k, v in sig.items()}, "strategie": nom, "actif": n, "epic": actif.epic, "jour": jour,
                 "statut": "attente", "contrats": contrats, "signal_le": maintenant().isoformat(timespec="seconds"),
                 "expire": (maintenant() + timedelta(minutes=max(minutes, 1))).isoformat(timespec="seconds"),
                 "pm": pm}
            self.etat["en_cours"].append(t)
            self.modifie = True
            print("Signal", t)
            if sig["ordre"] == "marche":
                self.entrer(t, m)
            return

    def entrer(self, t, m):
        actif = self.marches[t["actif"]]
        if self.essai:
            t["statut"] = "annule"
            t["motif"] = "essai (aucun ordre)"
            # mode essai : rien sur le téléphone, le signal est seulement noté dans l'état
            print(f"[essai] {t['strategie']} {t['actif']} sens {t['sens']} entrée {t['entree']} "
                  f"{t['contrats']} contrats")
            return
        ref = f"{PREFIXE_REF}{t['strategie'][:6]}{int(time.time())}"[:30]
        stop, cible = actif.arrondi(t["stop"]), actif.arrondi(t["cible"])
        try:
            conf = self.ig.ouvrir_marche(actif.epic, t["sens"], t["contrats"], stop, cible, ref, actif.devise)
        except ErreurIG as e:
            t["statut"], t["motif"] = "annule", f"refus IG : {e}"[:200]
            notifier(f"Ordre refusé par IG ({t['actif']}) : {str(e)[:120]}", tags="warning")
            return
        if not conf or conf.get("dealStatus") != "ACCEPTED":
            t["statut"], t["motif"] = "annule", f"refus IG : {(conf or {}).get('reason')}"
            notifier(f"Ordre refusé par IG ({t['actif']}) : {(conf or {}).get('reason')}", tags="warning")
            return
        prix = float(conf.get("level") or t["entree"])
        # le stop et l'objectif restent à la même distance que prévu si l'exécution a glissé
        t.update({"statut": "ouvert", "deal_id": conf.get("dealId"), "reference": ref, "entree_reelle": prix,
                  "stop_initial": t["stop"], "ouvert_le": maintenant().isoformat(timespec="seconds"),
                  "verifie": str(pd.Timestamp(maintenant()).floor("min"))})
        self.garde.ouvert(t["strategie"], t["actif"], t["jour"])
        notifier(f"{NOMS.get(t['strategie'], t['strategie'])} · {'Achat' if t['sens'] == 1 else 'Vente'} "
                 f"{t['actif']} à {prix} ({t['contrats']} contrats, IG démo)\n"
                 f"Stop {stop} · Objectif {cible} ({t['r_prevu']}R prévu)", tags="chart_with_upwards_trend")
        self.modifie = True

    def suivre(self, t, m, positions):
        actif = self.marches[t["actif"]]
        pm, _, jsem = heure_paris()
        if t["statut"] == "attente":
            depuis = pd.Timestamp(t["signal_le"]).floor("min")
            nouv = m.df[m.df.index >= depuis]
            touche = any((b.low <= t["entree"]) if t["sens"] == 1 else (b.high >= t["entree"])
                         for _, b in nouv.iterrows())
            if touche:
                prix = float(m.c[-1])
                # entrée seulement si le prix reste entre le stop et l'objectif
                if (prix - t["stop"]) * t["sens"] > stop_min(prix, actif.ap) and (t["cible"] - prix) * t["sens"] > 0:
                    self.entrer(t, m)
                else:
                    t["statut"], t["motif"] = "annule", "prix déjà au-delà"
            elif maintenant() >= datetime.fromisoformat(t["expire"]):
                t["statut"], t["motif"] = "annule", "expiré"
            self.modifie = True
            return

        if t["statut"] != "ouvert":
            return
        pos = next((p for p in positions if p["position"].get("dealId") == t.get("deal_id")), None)
        if pos is None:
            self.cloture_constatee(t, m)
            return
        # break-even à +1R
        if t.get("be_r") and not t.get("be"):
            seuil = t["entree_reelle"] + t["sens"] * t["be_r"] * abs(t["entree_reelle"] - t["stop_initial"])
            nouv = m.df[m.df.index >= pd.Timestamp(t["verifie"])]
            if any(((b.high >= seuil) if t["sens"] == 1 else (b.low <= seuil)) for _, b in nouv.iterrows()):
                try:
                    self.ig.modifier_stop(t["deal_id"], actif.arrondi(t["entree_reelle"]), actif.arrondi(t["cible"]))
                    t["stop"], t["be"] = t["entree_reelle"], True
                    notifier(f"{NOMS.get(t['strategie'], t['strategie'])} · {t['actif']} : +1R, stop au prix d'entrée.",
                             tags="shield")
                except ErreurIG as e:
                    print("Break-even refusé :", e)
            if len(nouv):
                t["verifie"] = str(nouv.index[-1] + pd.Timedelta(minutes=1))
        if self._avant_fermeture(actif, pm, jsem, marge=15):
            try:
                conf = self.ig.fermer(t["deal_id"], t["sens"], t["contrats"])
                self.finir(t, float((conf or {}).get("level") or m.c[-1]), "cloture")
            except ErreurIG as e:
                print("Clôture avant week-end refusée :", e)

    def cloture_constatee(self, t, m):
        """La position n'existe plus chez IG : stop ou objectif touché. On cherche le prix de sortie."""
        sortie, motif = None, None
        try:
            for act in self.ig.activite((pd.Timestamp(t["ouvert_le"]) - pd.Timedelta(minutes=1))
                                        .strftime("%Y-%m-%dT%H:%M:%S")):
                det = act.get("details") or {}
                if any(x.get("affectedDealId") == t["deal_id"] and "CLOSED" in (x.get("actionType") or "")
                       for x in det.get("actions", [])):
                    sortie = float(det.get("level") or 0) or None
                    if sortie:
                        break
        except ErreurIG as e:
            print("Historique IG indisponible :", e)
        if sortie is None:
            sortie = t["stop"] if (m.c[-1] - t["stop"]) * t["sens"] <= (t["cible"] - m.c[-1]) * t["sens"] else t["cible"]
        if abs(sortie - t["cible"]) <= abs(sortie - t["stop"]):
            motif = "objectif"
        else:
            motif = "break-even" if t.get("be") else "stop"
        self.finir(t, sortie, motif)

    def finir(self, t, sortie, motif):
        actif = self.marches[t["actif"]]
        r = resultat_r(t["sens"], t["entree_reelle"], sortie, t["stop_initial"], actif.ap, t.get("pm"))
        t.update({"statut": "ferme", "sortie": sortie, "motif": motif, "r": r,
                  "ferme_le": maintenant().isoformat(timespec="seconds")})
        self.garde.ferme(t["strategie"], t["jour"], r)
        self.etat["cycle"]["trades"] += 1
        notifier(f"{NOMS.get(t['strategie'], t['strategie'])} · {t['actif']} fermé ({motif}) : {r:+.2f}R\n"
                 f"Cycle : {self.etat['cycle']['trades']}/{self.etat['cycle']['quota']} trades",
                 tags="white_check_mark" if r > 0 else "x")
        if self.etat["cycle"]["trades"] >= self.etat["cycle"]["quota"]:
            notifier("Cycle terminé : le bilan part au Trésorier.", tags="trophy")
        self.modifie = True

    def _avant_fermeture(self, actif, pm, jsem, marge):
        """CFD fermés le week-end : pas d'entrée et clôture le vendredi avant 23 h (heure de Paris)."""
        if not actif.a.get("ferme_week_end", True):
            return False
        return jsem == 4 and pm >= 23 * 60 - marge or jsem == 5

    def reconcilier(self, positions):
        """Positions des bots (QGB) inconnues de l'état : fermées par prudence. Celles de Rayan : jamais."""
        suivis = {t.get("deal_id") for t in self.etat["en_cours"]}
        for p in positions:
            d = p["position"]
            if d.get("dealId") not in suivis and not self.essai:
                try:
                    self.ig.fermer(d["dealId"], 1 if d.get("direction") == "BUY" else -1, d.get("size"))
                    notifier(f"Position de bot non suivie fermée au démarrage ({p['market'].get('epic')}).",
                             tags="warning")
                except ErreurIG as e:
                    print("Réconciliation :", e)

    # -------------------------------------------------------------- boucle
    def erreur(self, txt):
        print("Erreur", txt)
        l = self.etat.setdefault("erreurs", [])
        if not l or l[-1].get("txt") != txt[:300]:
            l.append({"t": maintenant().isoformat(timespec="seconds"), "txt": txt[:300]})
            self.etat["erreurs"] = l[-20:]
            self.modifie = True

    def tour(self):
        try:
            positions = self.ig.positions_bots() if any(t["statut"] == "ouvert" for t in self.etat["en_cours"]) else []
        except ErreurIG as e:
            print("Positions IG indisponibles :", e)
            return
        for n in self.marches:
            try:
                df = self.barres[n]
                if len(df) < 300 or self.derniere.get(n) == df.index[-1]:
                    continue
                self.derniere[n] = df.index[-1]
                m = construire_marche(df, self.marches[n].ap, self.cfg)
                for t in [t for t in self.etat["en_cours"] if t["actif"] == n]:
                    self.suivre(t, m, positions)
                self.chercher(n, m)
            except Exception as exc:
                self.erreur(f"{n} : {exc!r}")
        termines = [t for t in self.etat["en_cours"] if t["statut"] in ("ferme", "annule")]
        if termines:
            self.etat["historique"] = (self.etat["historique"] + [t for t in termines if t["statut"] == "ferme"])[-500:]
            self.etat.setdefault("signaux_annules", [])
            self.etat["signaux_annules"] = (self.etat["signaux_annules"] +
                                            [t for t in termines if t["statut"] == "annule"])[-100:]
            self.etat["en_cours"] = [t for t in self.etat["en_cours"] if t not in termines]
            self.modifie = True

    def sauver(self, pousser=False):
        cycle = [t for t in self.etat["historique"] if t.get("ferme_le", "") >= self.etat["cycle"]["debut"]]
        self.etat["stats_cycle"] = {nom: statistiques([t["r"] for t in cycle if t["strategie"] == nom])
                                    for nom in self.cfg["strategies"]}
        self.etat["stats_cycle"]["global"] = statistiques([t["r"] for t in cycle])
        try:
            self.etat["capital"] = round(self.capital(), 2)
            self.etat["devise"] = self.ig.devise
        except Exception:
            pass
        self.etat["mode"] = "essai" if self.essai else "direct"
        self.etat["maj"] = maintenant().isoformat(timespec="seconds")
        self.etat["garde"] = self.garde.e
        ecrire_json("etat_ig.json", self.etat)
        self.garder_barres()
        self.modifie = False
        if pousser:
            git_pousser()

    def lancer(self, minutes):
        fin = time.time() + minutes * 60
        self.initialiser_barres()
        if not self.essai:
            try:
                self.reconcilier(self.ig.positions_bots())
            except ErreurIG as e:
                print("Réconciliation impossible :", e)
        if not self.essai:
            notifier(f"Bots IG démarrés : {', '.join(self.marches)}. Cycle {self.etat['cycle']['numero']} : "
                     f"{self.etat['cycle']['trades']}/{self.etat['cycle']['quota']} trades.", tags="rocket")
        minute_vue = None
        while time.time() < fin and not ARRET:
            try:
                self.echantillonner()
            except ErreurIG as e:
                self.erreur(f"prix IG : {e}")
                time.sleep(10)
            except Exception as e:
                self.erreur(f"échantillonnage : {e!r}")
            m_now = pd.Timestamp(maintenant()).floor("min")
            if minute_vue != m_now and maintenant().second >= 2:
                minute_vue = m_now
                self.clore_bougies_passees()
                _, jour, _ = heure_paris()
                self.garde.nettoyer(jour)
                self.tour()
                if time.time() - self.dernier_push > 600:
                    self.sauver(pousser=True)
                    self.dernier_push = time.time()
                elif self.modifie:
                    self.sauver()
            time.sleep(5)
        self.sauver(pousser=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=340)
    ap.add_argument("--essai", action="store_true")
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, _arret)
    signal.signal(signal.SIGINT, _arret)
    cfg = charger_config()
    essai = args.essai or not cfg.get("direct", {}).get("actif", True)
    if essai and not args.essai:
        print("direct.actif = false : le bot IG tourne en mode essai (aucun ordre).")
    Bot(essai=essai).lancer(args.minutes)


if __name__ == "__main__":
    main()
