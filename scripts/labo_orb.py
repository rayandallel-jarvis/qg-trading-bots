"""Opening Range Breakout 5 min (règles publiées, sans réglage) — backtest 2022 → oct. 2026.

Règles (Zarattini, Barbon et Aziz, 2023, « Can Day Trading Really Be Profitable? », QQQ) :
- première bougie de 5 min de la séance (New York 9 h 30 ; DAX 9 h 00 Francfort) ;
- bougie haussière → achat à l'ouverture de la bougie suivante ; baissière → vente ; sans corps → pas de trade ;
- stop à l'autre extrême de la première bougie ; objectif 10R ; sinon sortie à la clôture de la séance ;
- un trade par jour et par indice.
Coûts IG (écart + glissement) comme dans backtest.py. Compte 10 000 $, 0,5 % risqué (50 $ par R).
Usage : python3 scripts/labo_orb.py sortie.json [ACTIF ...]
"""
import collections, json, os, sys
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import numpy as np, pandas as pd
from bots.commun import charger_config, cout_unitaire, stop_min
from bots.donnees_duka import historique

FIN, DEBUT = date(2026, 10, 8), date(2021, 12, 1)
SEANCES = {"NAS100": ("America/New_York", "09:30", "16:00"), "US500": ("America/New_York", "09:30", "16:00"),
           "US30": ("America/New_York", "09:30", "16:00"), "DAX": ("Europe/Berlin", "09:00", "17:30")}
V1R = 50.0


def orb(df, a, tz, ouv, clo, minutes=5, objectif_r=10.0, sens_ok=(1, -1)):
    loc = df.tz_convert(tz)
    trades = []
    for jour, j in loc.groupby(loc.index.date):
        s = j.between_time(ouv, clo, inclusive="left")
        if len(s) < 60:
            continue
        t0 = s.index[0]
        if t0.strftime("%H:%M") != ouv:
            continue
        p = s[s.index < t0 + pd.Timedelta(minutes=minutes)]
        reste = s[s.index >= t0 + pd.Timedelta(minutes=minutes)]
        if len(p) < minutes - 1 or not len(reste):
            continue
        o, c, h, l = p["open"].iloc[0], p["close"].iloc[-1], p["high"].max(), p["low"].min()
        if c == o:
            continue
        sens = 1 if c > o else -1
        if sens not in sens_ok:
            continue
        entree = reste["open"].iloc[0]
        stop = l if sens == 1 else h
        risque = (entree - stop) * sens
        if risque <= 0 or risque < stop_min(entree, a):
            continue
        cible = entree + sens * objectif_r * risque
        sortie, motif = reste["close"].iloc[-1], "cloture"
        for oo, hh, ll in reste[["open", "high", "low"]].to_numpy():
            if (sens == 1 and ll <= stop) or (sens == -1 and hh >= stop):
                sortie, motif = (min(stop, oo) if sens == 1 else max(stop, oo)), "stop"
                break
            if (sens == 1 and hh >= cible) or (sens == -1 and ll <= cible):
                sortie, motif = cible, "objectif"
                break
        pm = t0.tz_convert("Europe/Paris")
        cout = cout_unitaire(entree, a, pm.hour * 60 + pm.minute)
        brut = (sortie - entree) * sens / risque
        trades.append({"jour": str(jour), "sens": sens, "entree": float(entree), "stop": float(stop),
                       "sortie": float(sortie), "motif": motif, "r_brut": round(float(brut), 3),
                       "r": round(float(brut - cout / risque), 3), "risque_pts": float(risque),
                       "levier": round(float(10000 * 0.005 / risque * entree / 10000), 2)})
    return trades


def mesures(tr):
    tr = sorted(tr, key=lambda t: t["jour"])
    if not tr:
        return {"trades": 0}
    mois, ans = collections.defaultdict(float), collections.defaultdict(float)
    cum = pic = dd = 0.0
    for t in tr:
        mois[t["jour"][:7]] += t["r"] * V1R; ans[t["jour"][:4]] += t["r"] * V1R
        cum += t["r"] * V1R; pic = max(pic, cum); dd = min(dd, cum - pic)
    return {"trades": len(tr), "trades_mois": round(len(tr) / len(mois), 1), "gain": round(cum),
            "r_trade": round(cum / V1R / len(tr), 3), "brut_trade": round(sum(t["r_brut"] for t in tr) / len(tr), 3),
            "reussite": round(sum(t["r"] > 0 for t in tr) / len(tr), 2), "pire_baisse": round(dd),
            "mois_pos": f"{sum(v > 0 for v in mois.values())}/{len(mois)}", "pire_mois": round(min(mois.values())),
            "par_an": {k: round(v) for k, v in sorted(ans.items())}, "mois": {k: round(v) for k, v in sorted(mois.items())},
            "levier_median": float(np.median([t["levier"] for t in tr]))}


if __name__ == "__main__":
    cfg = charger_config()
    actifs = sys.argv[2:] or list(SEANCES)
    sortie = {}
    for sym in actifs:
        a = cfg["actifs"][sym]
        df = historique(a["instrument"], (FIN - DEBUT).days + 1, fin=FIN, travailleurs=8)
        tz, ouv, clo = SEANCES[sym]
        tr = orb(df, a, tz, ouv, clo)
        sortie[sym] = {"tout": mesures(tr), "achat": mesures([t for t in tr if t["sens"] == 1]),
                       "vente": mesures([t for t in tr if t["sens"] == -1]), "trades": tr}
        for k in ("tout", "achat", "vente"):
            e = sortie[sym][k]
            print(f'{sym:7s} {k:6s} {e["trades"]:5d} tr ({e["trades_mois"]}/mois) {e["gain"]:7d} $ '
                  f'R/tr {e["r_trade"]:+.3f} brut {e["brut_trade"]:+.3f} réussite {e["reussite"]} '
                  f'baisse {e["pire_baisse"]} mois+ {e["mois_pos"]} levier {e["levier_median"]} an {e["par_an"]}', flush=True)
    json.dump(sortie, open(sys.argv[1], "w"), default=str, ensure_ascii=False)
    print("FIN")
