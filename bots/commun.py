"""Outils partagés : configuration, coûts, taille de position, garde-fous, notifications, statistiques."""
import json
import os
import urllib.request
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent.parent
ETAT = RACINE / "etat"


def charger_config():
    with open(RACINE / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def cout_unitaire(prix, a):
    """Frais + glissement aller-retour, par unité d'actif."""
    frais = 2 * prix * a.get("frais_pct", 0) / 100
    gliss = 2 * (a.get("glissement", 0) + prix * a.get("glissement_pct", 0) / 100)
    return frais + gliss


def taille(signal, capital, a, cfg):
    """Quantité, risque monétaire et raison de refus éventuelle."""
    r = cfg["risque"]
    risque_u = abs(signal["entree"] - signal["stop"])
    cout = cout_unitaire(signal["entree"], a)
    if cout > r["frais_max_part_risque"] * risque_u:
        return 0, 0, "frais trop lourds"
    qte = capital * r["risque_par_trade_pct"] / 100 / risque_u
    qte = min(qte, capital * a.get("notionnel_max_pct", 100) / 100 / signal["entree"])
    qte = int(qte) if a["type"] == "action" else round(qte, 4)
    if qte <= 0 or qte * signal["entree"] < 10:
        return 0, 0, "taille trop petite"
    return qte, qte * risque_u, None


def resultat_r(sens, entree, sortie, stop_initial, a):
    risque_u = abs(entree - stop_initial)
    brut = (sortie - entree) * sens
    net = brut - cout_unitaire(entree, a)
    return round(net / risque_u, 3) if risque_u > 0 else 0.0


class Garde:
    """Plafond de trades par jour et coupe-circuit après N pertes d'affilée."""

    def __init__(self, cfg, etat=None):
        self.cfg = cfg
        self.e = etat if etat is not None else {}

    def _cle(self, strat, actif, jour):
        return f"{strat}|{actif}|{jour}"

    def autorise(self, strat, actif, jour):
        d = self.e.get(self._cle(strat, actif, jour), {"n": 0, "pertes": 0})
        if d["n"] >= self.cfg["strategies"][strat]["max_trades_jour"]:
            return False
        jstrat = self.e.get(f"{strat}|*|{jour}", {"pertes": 0})
        return jstrat["pertes"] < self.cfg["risque"]["pertes_consecutives_max"]

    def ouvert(self, strat, actif, jour):
        d = self.e.setdefault(self._cle(strat, actif, jour), {"n": 0, "pertes": 0})
        d["n"] += 1

    def ferme(self, strat, jour, r):
        d = self.e.setdefault(f"{strat}|*|{jour}", {"pertes": 0})
        d["pertes"] = d["pertes"] + 1 if r < 0 else 0

    def nettoyer(self, jour):
        self.e = {k: v for k, v in self.e.items() if k.endswith(jour)}


def statistiques(rs):
    n = len(rs)
    if n == 0:
        return {"trades": 0}
    gains = [r for r in rs if r > 0]
    pertes = [r for r in rs if r <= 0]
    cum, pic, dd = 0.0, 0.0, 0.0
    for r in rs:
        cum += r
        pic = max(pic, cum)
        dd = min(dd, cum - pic)
    pf = sum(gains) / abs(sum(pertes)) if pertes and sum(pertes) != 0 else None
    return {
        "trades": n,
        "esperance_r": round(sum(rs) / n, 3),
        "taux_reussite": round(len(gains) / n, 3),
        "profit_factor": round(pf, 2) if pf is not None else None,
        "total_r": round(cum, 2),
        "drawdown_max_r": round(dd, 2),
    }


def notifier(message, titre="QG Trading", tags="robot"):
    sujet = os.environ.get("NTFY_TOPIC")
    if not sujet:
        print("[ntfy]", message)
        return
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{sujet}", data=message.encode("utf-8"), method="POST",
                                     headers={"Title": titre,
                                              "Tags": tags})
        urllib.request.urlopen(req, timeout=15)
    except Exception as exc:  # une notification ratée ne doit jamais arrêter un bot
        print("ntfy en échec :", exc)


def ecrire_json(nom, donnees):
    ETAT.mkdir(exist_ok=True)
    with open(ETAT / nom, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=1, default=str)


def lire_json(nom, defaut):
    try:
        with open(ETAT / nom, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return defaut
