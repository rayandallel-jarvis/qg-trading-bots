"""Laboratoire des bots Perso : teste des variantes de réglages sur 8 actifs.

Règle anti sur-réglage : les variantes se comparent sur la période de RÉGLAGE (8 premiers mois).
Les 4 derniers mois (EXAMEN) ne servent qu'une fois, à la fin (option --examen).

Usage : python3 scripts/labo_perso.py variantes.json sortie.json [--examen]
variantes.json = {"nom": {"perso_1h": {param: valeur}, "perso_2h": {...}}, ...}
"""
import copy, json, os, sys, collections
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pandas as pd
from bots.commun import charger_config

ACTIFS = ["NAS100", "OR", "EURUSD", "US30", "US500", "DAX", "GBPUSD", "USDJPY"]
STRATS = ["perso_1h", "perso_2h"]
JOURS = 390
COUPURE = "2026-06-08"        # avant = réglage (8 mois), après = examen (4 mois)
CHAMPS = ("strategie", "actif", "sens", "debut", "fin", "r", "r_brut", "motif", "etoiles", "pm")


def _travail(args):
    sym, variantes = args
    from bots.backtest import construire_marche, simuler
    from bots.donnees_duka import historique
    cfg0 = charger_config()
    a = cfg0["actifs"][sym]
    df = historique(a["instrument"], JOURS, travailleurs=6)
    debut = str(df.index.min() + pd.Timedelta(days=20))
    m = construire_marche(df, a, cfg0)
    out = {}
    for nom, reglages in variantes.items():
        cfg = copy.deepcopy(cfg0)
        for strat, p in reglages.items():
            if strat == "risque":
                cfg["risque"].update(p)
            else:
                cfg["strategies"][strat].update(p)
        tr = []
        for strat in STRATS:
            if reglages.get(strat, {}).get("actif", True) is False:
                continue
            tr += [{k: t.get(k) for k in CHAMPS} for t in simuler(m, strat, sym, a, cfg) if t["debut"] >= debut]
        out[nom] = tr
    return sym, out


def mesures(tr, valeur_1r=50.0):
    """Mesures en dollars sur un compte de 10 000 $ (0,5 % par trade = 50 $ par R)."""
    tr = sorted(tr, key=lambda t: t["fin"])
    if not tr:
        return {"trades": 0, "gain": 0}
    mois = collections.defaultdict(float)
    for t in tr:
        mois[t["fin"][:7]] += t["r"] * valeur_1r
    cum, pic, dd = 0.0, 0.0, 0.0
    for t in tr:
        cum += t["r"] * valeur_1r
        pic = max(pic, cum); dd = min(dd, cum - pic)
    n_mois = max(1, len(mois))
    return {"trades": len(tr), "trades_mois": round(len(tr) / n_mois, 1),
            "gain": round(sum(t["r"] for t in tr) * valeur_1r), "r_trade": round(sum(t["r"] for t in tr) / len(tr), 3),
            "brut_trade": round(sum(t["r_brut"] for t in tr) / len(tr), 3),
            "reussite": round(sum(t["r"] > 0 for t in tr) / len(tr), 2), "pire_baisse": round(dd),
            "mois_pos": f"{sum(v > 0 for v in mois.values())}/{len(mois)}",
            "mois": {k: round(v) for k, v in sorted(mois.items())}}


def periode(tr, examen):
    return [t for t in tr if (t["fin"] >= COUPURE) == examen]


if __name__ == "__main__":
    variantes = json.load(open(sys.argv[1]))
    examen = "--examen" in sys.argv
    with ProcessPoolExecutor(2) as ex:
        res = dict(ex.map(_travail, [(s, variantes) for s in ACTIFS]))
    sortie = {}
    for nom in variantes:
        tous = [t for s in ACTIFS for t in res[s][nom]]
        tous = periode(tous, examen)
        combos = collections.defaultdict(list)
        for t in tous:
            combos[f'{t["strategie"]}|{t["actif"]}'].append(t)
        sortie[nom] = {"ensemble": mesures(tous), "combos": {k: mesures(v) for k, v in sorted(combos.items())},
                       "trades": tous}
    json.dump(sortie, open(sys.argv[2], "w"), default=str, ensure_ascii=False)
    for nom, x in sortie.items():
        e = x["ensemble"]
        print(f'== {nom}: {e["trades"]} trades ({e.get("trades_mois")}/mois) {e["gain"]} $ '
              f'R/trade {e.get("r_trade")} brut {e.get("brut_trade")} baisse {e.get("pire_baisse")} mois+ {e.get("mois_pos")}')
        for k, c in x["combos"].items():
            print(f'   {k:22s} {c["trades"]:4d} {c["gain"]:6d} $  {c["r_trade"]:+.3f}R  mois+ {c["mois_pos"]}')
    print("FIN")
