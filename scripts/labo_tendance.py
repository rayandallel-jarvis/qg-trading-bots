"""Bots Perso sur les indices, de janvier 2022 à aujourd'hui (2022 = année de baisse du Nasdaq).
Compare achat seul / filtre de tendance 4 h ou daily. Résultats année par année et mois par mois.

Usage : python3 scripts/labo_tendance.py variantes.json sortie.json [ACTIF ...]
"""
import copy, json, os, sys, collections
from datetime import date
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pandas as pd
from bots.commun import charger_config

FIN = date(2026, 10, 8)
DEBUT = date(2021, 12, 1)            # décembre 2021 = chauffe des indicateurs, trades comptés dès 2022
STRATS = ["perso_1h", "perso_2h"]
CHAMPS = ("strategie", "actif", "sens", "debut", "fin", "r", "r_brut", "motif", "etoiles", "pm", "entree",
          "stop_initial", "cible", "sortie", "dessin", "fvg", "htf")


def _travail(args):
    sym, variantes = args
    from bots.backtest import construire_marche, simuler
    from bots.donnees_duka import historique
    cfg0 = charger_config()
    a = cfg0["actifs"][sym]
    df = historique(a["instrument"], (FIN - DEBUT).days + 1, fin=FIN, travailleurs=8)
    m = construire_marche(df, a, cfg0)
    out = {}
    for nom, reglages in variantes.items():
        cfg = copy.deepcopy(cfg0)
        for strat, p in reglages.items():
            cfg["strategies"][strat].update(p)
        tr = []
        for strat in STRATS:
            if reglages.get(strat, {}).get("actif", True) is False:
                continue
            tr += [{k: t.get(k) for k in CHAMPS} for t in simuler(m, strat, sym, a, cfg) if t["debut"] >= "2022-01-01"]
        out[nom] = tr
    prix = df["close"].resample("1D").last().dropna()
    return sym, out, {str(k.date()): float(v) for k, v in prix.items()}


def mesures(tr, v1r=50.0):
    tr = sorted(tr, key=lambda t: t["fin"])
    if not tr:
        return {"trades": 0, "gain": 0}
    mois, ans = collections.defaultdict(float), collections.defaultdict(float)
    for t in tr:
        mois[t["fin"][:7]] += t["r"] * v1r
        ans[t["fin"][:4]] += t["r"] * v1r
    cum = pic = dd = 0.0
    for t in tr:
        cum += t["r"] * v1r; pic = max(pic, cum); dd = min(dd, cum - pic)
    return {"trades": len(tr), "trades_mois": round(len(tr) / max(1, len(mois)), 1),
            "gain": round(cum), "r_trade": round(cum / v1r / len(tr), 3),
            "brut_trade": round(sum(t["r_brut"] for t in tr) / len(tr), 3),
            "reussite": round(sum(t["r"] > 0 for t in tr) / len(tr), 2), "pire_baisse": round(dd),
            "mois_pos": f"{sum(v > 0 for v in mois.values())}/{len(mois)}",
            "pire_mois": round(min(mois.values())), "meilleur_mois": round(max(mois.values())),
            "par_an": {k: round(v) for k, v in sorted(ans.items())},
            "mois": {k: round(v) for k, v in sorted(mois.items())}}


if __name__ == "__main__":
    variantes = json.load(open(sys.argv[1]))
    actifs = sys.argv[3:] or ["NAS100", "US500", "US30", "DAX"]
    with ProcessPoolExecutor(2) as ex:
        res = {s: (o, p) for s, o, p in ex.map(_travail, [(s, variantes) for s in actifs])}
    sortie = {"prix": {s: res[s][1] for s in actifs}, "variantes": {}}
    for nom in variantes:
        tous = [t for s in actifs for t in res[s][0][nom]]
        combos = collections.defaultdict(list)
        for t in tous:
            combos[f'{t["strategie"]}|{t["actif"]}'].append(t)
        sortie["variantes"][nom] = {"ensemble": mesures(tous),
                                    "combos": {k: mesures(v) for k, v in sorted(combos.items())}, "trades": tous}
    json.dump(sortie, open(sys.argv[2], "w"), default=str, ensure_ascii=False)
    for nom, x in sortie["variantes"].items():
        e = x["ensemble"]
        print(f'== {nom}: {e["trades"]} tr ({e.get("trades_mois")}/mois) {e["gain"]} $ R/tr {e.get("r_trade")} '
              f'brut {e.get("brut_trade")} baisse {e.get("pire_baisse")} mois+ {e.get("mois_pos")} an {e.get("par_an")}')
        for k, c in x["combos"].items():
            print(f'   {k:20s} {c["trades"]:4d} {c["gain"]:6d} $ {c["r_trade"]:+.3f}R mois+ {c["mois_pos"]} an {c["par_an"]}')
    print("FIN")
