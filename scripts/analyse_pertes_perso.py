"""Analyse des pertes des bots Perso (1 an) + test de la marge du stop + résultats mois par mois.
Compte 10 000 $, 0,5 % par trade, sans break-even. Résultat : etat/analyse_pertes_perso.json"""
import copy, json, os, sys, collections
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import numpy as np, pandas as pd
from bots.commun import charger_config, statistiques
from bots.backtest import construire_marche, simuler, en_valeur, REFUS
from bots.donnees_duka import historique

JOURS, APRES_H = 365, 48
MARGES = [0.1, 0.3, 0.5, 1.0]
cfg0 = charger_config()
res = {m: [] for m in MARGES}
donnees = {}
for sym in ["NAS100", "OR", "EURUSD"]:
    a = cfg0["actifs"][sym]
    df = historique(a["instrument"], JOURS + 20, travailleurs=12)
    donnees[sym] = df
    debut = str(df.index.min() + pd.Timedelta(days=20))
    print(sym, flush=True)
    for marge in MARGES:
        cfg = copy.deepcopy(cfg0)
        for nom in ("perso_1h", "perso_2h"):
            cfg["strategies"][nom]["break_even_r"] = None
            cfg["strategies"][nom]["marge_stop_atr"] = marge
        m = construire_marche(df, a, cfg)
        for nom in ("perso_1h", "perso_2h"):
            res[marge] += [t for t in simuler(m, nom, sym, a, cfg) if t["debut"] >= debut]

def analyse_perte(t):
    df = donnees[t["actif"]]
    d0, d1 = pd.Timestamp(t["debut"]), pd.Timestamp(t["fin"])
    s, e, st = t["sens"], t["entree"], t["stop_initial"]
    R = abs(e - st)
    pend = df.loc[d0:d1]
    mfe = ((pend["high"].max() - e) if s == 1 else (e - pend["low"].min())) / R
    apres = df.loc[d1:d1 + pd.Timedelta(hours=APRES_H)]
    cible = e + s * 2 * R
    touche = apres.index[(apres["high"] >= cible) if s == 1 else (apres["low"] <= cible)]
    if len(touche):
        avant = apres.loc[:touche[0]]
        depasse = ((st - avant["low"].min()) if s == 1 else (avant["high"].max() - st)) / R
    else:
        depasse = None
    return {"mfe_r": round(float(mfe), 2), "duree_min": int((d1 - d0).total_seconds() // 60),
            "cible_apres_stop": bool(len(touche)), "depassement_stop_r": None if depasse is None else round(float(depasse), 2)}

base = res[0.1]
pertes = [t for t in base if t["r"] < 0]
an = [analyse_perte(t) for t in pertes]
cat = collections.Counter()
for x in an:
    if x["cible_apres_stop"] and x["depassement_stop_r"] <= 0.5:
        cat["stop_chasse_puis_objectif_(dépassement≤0,5R)"] += 1
    elif x["cible_apres_stop"]:
        cat["objectif_atteint_apres_mais_stop_large_depasse"] += 1
    elif x["mfe_r"] >= 1:
        cat["etait_a_+1R_puis_stop"] += 1
    elif x["mfe_r"] < 0.3:
        cat["jamais_en_gain_(<0,3R)"] += 1
    else:
        cat["un_peu_en_gain_puis_stop"] += 1
dep = sorted(x["depassement_stop_r"] for x in an if x["depassement_stop_r"] is not None)

def mensuel(tr):
    g = collections.defaultdict(float)
    for t in tr:
        g[t["fin"][:7]] += t["r"] * 50
    return {k: round(v) for k, v in sorted(g.items())}

def bilan(tr):
    rs = [t["r"] for t in sorted(tr, key=lambda t: t["fin"])]
    st = statistiques(rs); st["valeur"] = en_valeur(rs)
    mo = mensuel(tr); st["mois"] = mo
    st["mois_positifs"] = f'{sum(v > 0 for v in mo.values())}/{len(mo)}'
    return st

sortie = {"marges": {}, "pertes": {"nombre": len(pertes), "categories": dict(cat),
          "duree_mediane_min": int(np.median([x["duree_min"] for x in an])),
          "mfe_median_r": float(np.median([x["mfe_r"] for x in an])),
          "depassement_quartiles_r": [dep[len(dep) // 4], dep[len(dep) // 2], dep[3 * len(dep) // 4]] if dep else None},
          "detail_pertes": [{**{k: t[k] for k in ("strategie", "actif", "debut", "fin", "sens", "etoiles")}, **x}
                            for t, x in zip(pertes, an)]}
for marge, tr in res.items():
    g = collections.defaultdict(list)
    for t in tr:
        g[f'{t["strategie"]}|{t["actif"]}'].append(t)
    sortie["marges"][str(marge)] = {"ensemble": bilan(tr), "detail": {k: bilan(v) for k, v in sorted(g.items())}}
json.dump(sortie, open(os.path.join(os.path.dirname(__file__), '..', 'etat', 'analyse_pertes_perso.json'), 'w'),
          default=str, ensure_ascii=False, indent=1)
print("FIN")
