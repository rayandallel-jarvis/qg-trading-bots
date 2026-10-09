"""Backtest 1 an des bots Perso (1 h et 2 h) sur Nasdaq, or, EUR/USD, avec et sans break-even.
Compte de 10 000 $, 0,5 % risqué par trade. Résultats dans etat/backtest_perso_1an.json."""
import copy, json, os, sys, collections
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pandas as pd
from bots.commun import charger_config, statistiques
from bots.backtest import construire_marche, simuler, en_valeur, REFUS
from bots.donnees_duka import historique

JOURS = 365
VARIANTES = {"be_1r": 1.0, "sans_be": None}
cfg0 = charger_config()
sortie = {"date": str(pd.Timestamp.now(tz="UTC")), "jours": JOURS, "compte": 10000, "risque_pct": 0.5,
          "variantes": {}}
trades = {v: [] for v in VARIANTES}
for sym in ["NAS100", "OR", "EURUSD"]:
    a = cfg0["actifs"][sym]
    df = historique(a["instrument"], JOURS + 20, travailleurs=12)
    debut = str(df.index.min() + pd.Timedelta(days=20))
    print(sym, len(df), flush=True)
    for v, be in VARIANTES.items():
        cfg = copy.deepcopy(cfg0)
        for nom in ("perso_1h", "perso_2h"):
            cfg["strategies"][nom]["break_even_r"] = be
        m = construire_marche(df, a, cfg)
        for nom in ("perso_1h", "perso_2h"):
            tr = [t for t in simuler(m, nom, sym, a, cfg) if t["debut"] >= debut]
            trades[v] += tr

def bilan(tr):
    rs = [t["r"] for t in sorted(tr, key=lambda t: t["fin"])]
    if not rs:
        return {"trades": 0}
    st = statistiques(rs); st["valeur"] = en_valeur(rs)
    st["brut"] = round(sum(t["r_brut"] for t in tr) / len(tr), 3)
    return st

for v, tr in trades.items():
    g = collections.defaultdict(list)
    for t in tr:
        g[f'{t["strategie"]}|{t["actif"]}'].append(t)
        g[f'score{t["etoiles"]}'].append(t)
        g[f'{t["strategie"]}|score{t["etoiles"]}'].append(t)
    sortie["variantes"][v] = {"ensemble": bilan(tr), "detail": {k: bilan(x) for k, x in sorted(g.items())}}
sortie["trades"] = trades
sortie["refus"] = dict(REFUS)
json.dump(sortie, open(os.path.join(os.path.dirname(__file__), '..', 'etat', 'backtest_perso_1an.json'), 'w'),
          default=str, ensure_ascii=False, indent=1)
print("FIN")
