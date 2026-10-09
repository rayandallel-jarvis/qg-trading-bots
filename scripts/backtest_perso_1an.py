import json, sys
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__file__), '..'))
from bots.commun import charger_config, statistiques
from bots.backtest import construire_marche, simuler, en_valeur, REFUS
from bots.donnees_duka import historique
cfg = charger_config()
JOURS = 365
res, tous = {}, []
for sym in ["NAS100", "OR", "EURUSD"]:
    a = cfg["actifs"][sym]
    df = historique(a["instrument"], JOURS + 20, travailleurs=12)
    print(sym, len(df), df.index.min(), flush=True)
    m = construire_marche(df, a, cfg)
    debut = str(df.index.min() + __import__('pandas').Timedelta(days=20))
    for nom in ["perso_1h", "perso_2h"]:
        tr = [t for t in simuler(m, nom, sym, a, cfg) if t["debut"] >= debut]
        tr.sort(key=lambda t: t["fin"])
        rs = [t["r"] for t in tr]
        st = statistiques(rs); st["valeur"] = en_valeur(rs)
        st["brut"] = round(sum(t.get("r_brut", t["r"]) for t in tr) / len(tr), 3) if tr else None
        res[f"{nom}|{sym}"] = st; tous += tr
        print(nom, sym, st, flush=True)
json.dump({"res": res, "trades": tous, "refus": dict(REFUS)}, open('etat/backtest_perso_1an_brut.json', 'w'), default=str)
print("FIN")
