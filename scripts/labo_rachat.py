"""Rachat de baisse en tendance haussière, en daily (règles de L. Connors, « RSI 2 », sans réglage) — 2013 → oct. 2026.

Règles : clôture > moyenne 200 jours ET RSI(2) < 10 → achat à la clôture ;
sortie à la clôture du jour où le prix repasse au-dessus de la moyenne 5 jours. Achat seulement, pas de stop.
Exposition = 10 000 $ (levier 1, comme acheter l'indice avec tout le compte).
Coûts IG : écart hors heures à l'entrée et à la sortie + financement de nuit 6 %/an du notionnel par jour calendaire.
Comparaison : acheter et garder l'indice sur la même période (levier 1).
Usage : python3 scripts/labo_rachat.py sortie.json
"""
import collections, json, os, sys
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import numpy as np, pandas as pd
from bots.commun import charger_config
from bots.donnees_duka import historique

ACTIFS = {"NAS100": "USATECH.IDX-USD", "US500": "USA500.IDX-USD", "US30": "USA30.IDX-USD", "DAX": "DEU.IDX-EUR"}
CAPITAL, FINANCEMENT_AN = 10000.0, 0.06
DOSSIER = os.path.join(os.path.dirname(__file__), '..', 'data', 'duka_jour')


def jours(sym):
    d = pd.read_csv(os.path.join(DOSSIER, ACTIFS[sym] + ".csv"), index_col=0, parse_dates=True)
    d.index = pd.to_datetime(d.index, utc=True)
    m = historique(ACTIFS[sym], 290, fin=date(2026, 10, 8), travailleurs=8)       # 2026 depuis les bougies 1 min
    m26 = m.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    d = pd.concat([d, m26[m26.index > d.index.max()]])
    return d[d.index.dayofweek < 5]


def rsi2(c):
    delta = c.diff()
    up, dn = delta.clip(lower=0), -delta.clip(upper=0)
    au = up.ewm(alpha=1 / 2, adjust=False).mean(); ad = dn.ewm(alpha=1 / 2, adjust=False).mean()
    return 100 - 100 / (1 + au / ad.replace(0, np.nan))


def strategie(d, a):
    c = d["close"]
    sma200, sma5, r = c.rolling(200).mean(), c.rolling(5).mean(), rsi2(c)
    ecart = a.get("ecart_hors", a["ecart_coeur"]) + 2 * a.get("glissement", 0)
    trades, pos = [], None
    for t in range(200, len(d)):
        if pos is None:
            if c.iloc[t] > sma200.iloc[t] and r.iloc[t] < 10:
                pos = (d.index[t], c.iloc[t])
        elif c.iloc[t] > sma5.iloc[t]:
            t0, p0 = pos
            p1 = c.iloc[t]
            nuits = (d.index[t] - t0).days
            brut = (p1 - p0) / p0
            net = brut - ecart / p0 - FINANCEMENT_AN / 365 * nuits
            trades.append({"entree_jour": str(t0.date()), "sortie_jour": str(d.index[t].date()), "jours": nuits,
                           "brut_pct": round(brut * 100, 3), "net_pct": round(net * 100, 3),
                           "gain": round(net * CAPITAL, 2)})
            pos = None
    return trades


def mesures(tr, debut, fin, prix):
    mois, ans = collections.defaultdict(float), collections.defaultdict(float)
    cum = pic = dd = 0.0
    for t in tr:
        mois[t["sortie_jour"][:7]] += t["gain"]; ans[t["sortie_jour"][:4]] += t["gain"]
        cum += t["gain"]; pic = max(pic, cum); dd = min(dd, cum - pic)
    n_mois = (fin.year - debut.year) * 12 + fin.month - debut.month + 1
    bh = prix.loc[debut:fin]
    ans_bh = {str(y): round((g.iloc[-1] / g.iloc[0] - 1) * CAPITAL) for y, g in bh.groupby(bh.index.year)}
    return {"trades": len(tr), "trades_mois": round(len(tr) / n_mois, 2), "gain": round(cum),
            "reussite": round(sum(t["gain"] > 0 for t in tr) / max(1, len(tr)), 2),
            "gain_moyen_pct": round(float(np.mean([t["net_pct"] for t in tr])), 3) if tr else None,
            "duree_moy_jours": round(float(np.mean([t["jours"] for t in tr])), 1) if tr else None,
            "pire_baisse": round(dd), "mois_pos": f"{sum(v > 0 for v in mois.values())}/{n_mois} (mois sans trade = 0)",
            "par_an": {k: round(v) for k, v in sorted(ans.items())},
            "acheter_garder": round((bh.iloc[-1] / bh.iloc[0] - 1) * CAPITAL), "acheter_garder_par_an": ans_bh,
            "mois": {k: round(v) for k, v in sorted(mois.items())}}


if __name__ == "__main__":
    cfg = charger_config()
    sortie = {}
    for sym in ACTIFS:
        d = jours(sym)
        tr = strategie(d, cfg["actifs"][sym])
        debut = d.index[200]
        e = mesures(tr, debut, d.index[-1], d["close"])
        sortie[sym] = {**e, "debut": str(debut.date()), "trades_detail": tr}
        print(f'{sym:7s} depuis {debut.date()} : {e["trades"]} trades ({e["trades_mois"]}/mois) {e["gain"]} $ '
              f'réussite {e["reussite"]} moy {e["gain_moyen_pct"]} % durée {e["duree_moy_jours"]} j '
              f'baisse {e["pire_baisse"]} | acheter-garder {e["acheter_garder"]} $', flush=True)
        print('   par an  ', e["par_an"]); print('   garder  ', e["acheter_garder_par_an"])
    json.dump(sortie, open(sys.argv[1], "w"), ensure_ascii=False, default=str)
    print("FIN")
