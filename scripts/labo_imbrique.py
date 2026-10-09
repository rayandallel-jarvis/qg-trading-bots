"""Test du modèle « OB dans un OB » sur 2026 (1er janvier → 8 octobre), Nasdaq, S&P 500, or.
Compte de 10 000 $, 0,5 % risqué par trade (50 $ = 1R), frais IG inclus.
Usage : python3 scripts/labo_imbrique.py sortie.json
"""
import collections, copy, json, os, sys
from datetime import date
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from bots.commun import charger_config

FIN, DEBUT, COMPTE_DES = date(2026, 10, 8), date(2025, 10, 15), "2026-01-01"
ACTIFS = ["NAS100", "US500", "OR"]
VARIANTES = {
    "modele": {},                               # objectif = liquidité 1 h la plus proche à ≥ 1,5R
    "objectif_2R": {"r_fixe": 2.0},
    "achat_seul": {"sens_autorise": 1},
}
V1R = 50.0


def _travail(sym):
    import bots.strategies as S
    from bots.backtest import construire_marche, simuler
    from bots.donnees_duka import historique
    cfg0 = charger_config()
    a = cfg0["actifs"][sym]
    df = historique(a["instrument"], (FIN - DEBUT).days + 1, fin=FIN, travailleurs=8)
    m = construire_marche(df, a, cfg0)
    out = {}
    for nom, reg in VARIANTES.items():
        cfg = copy.deepcopy(cfg0)
        cfg["strategies"]["imbrique"].update(reg)
        S.OB_UTILISES.clear()
        tr = simuler(m, "imbrique", sym, a, cfg)
        out[nom] = [t for t in tr if t["debut"] >= COMPTE_DES]
    return sym, out


def mesures(tr):
    tr = sorted(tr, key=lambda t: t["fin"])
    if not tr:
        return {"trades": 0, "gain": 0}
    mois = collections.defaultdict(float)
    cum = pic = dd = 0.0
    for t in tr:
        mois[t["fin"][:7]] += t["r"] * V1R
        cum += t["r"] * V1R; pic = max(pic, cum); dd = min(dd, cum - pic)
    return {"trades": len(tr), "gain": round(cum), "r_trade": round(cum / V1R / len(tr), 3),
            "brut_trade": round(sum(t["r_brut"] for t in tr) / len(tr), 3),
            "reussite": round(sum(t["r"] > 0 for t in tr) / len(tr), 2),
            "r_prevu_moyen": round(sum(t["r_prevu"] for t in tr) / len(tr), 2), "pire_baisse": round(dd),
            "mois_pos": f"{sum(v > 0 for v in mois.values())}/{len(mois)}",
            "mois": {k: round(v) for k, v in sorted(mois.items())}}


if __name__ == "__main__":
    with ProcessPoolExecutor(2) as ex:
        res = dict(ex.map(_travail, ACTIFS))
    sortie = {}
    for nom in VARIANTES:
        tous = [t for s in ACTIFS for t in res[s][nom]]
        sortie[nom] = {"ensemble": mesures(tous), "par_actif": {s: mesures(res[s][nom]) for s in ACTIFS},
                       "achats": mesures([t for t in tous if t["sens"] == 1]),
                       "ventes": mesures([t for t in tous if t["sens"] == -1]), "trades": tous}
        e = sortie[nom]["ensemble"]
        print(f'== {nom}: {e["trades"]} trades, {e["gain"]} $, réussite {e.get("reussite")}, R prévu {e.get("r_prevu_moyen")}, '
              f'R/trade {e.get("r_trade")} (brut {e.get("brut_trade")}), baisse {e.get("pire_baisse")}, mois+ {e.get("mois_pos")}')
        print("   mois", e.get("mois"))
        for s in ACTIFS:
            c = sortie[nom]["par_actif"][s]
            print(f'   {s:7s} {c["trades"]:3d} tr {c["gain"]:6d} $ réussite {c.get("reussite")} mois+ {c.get("mois_pos")}')
        for k in ("achats", "ventes"):
            c = sortie[nom][k]
            print(f'   {k:7s} {c["trades"]:3d} tr {c["gain"]:6d} $ réussite {c.get("reussite")}')
    json.dump(sortie, open(sys.argv[1], "w"), default=str, ensure_ascii=False)
    print("FIN")
