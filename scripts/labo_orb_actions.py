"""ORB 5 min sur les « Stocks in Play » (Zarattini, Barbon, Aziz, 2024) — actions US, 2026.

Règles du papier, sans réglage :
- univers du jour : prix d'ouverture > 5 $, ATR 14 jours > 0,50 $ ;
- volume relatif = volume des 5 premières minutes / moyenne des 5 premières minutes des 14 jours précédents ;
  « Stocks in Play » = volume relatif ≥ 100 %, puis les 20 plus forts du jour ;
- sens = couleur de la 1re bougie de 5 min (9 h 30 – 9 h 35, New York) ; sans corps → rien ;
- ordre stop au plus haut (achat) / plus bas (vente) de cette bougie, valable jusqu'à la clôture ;
- stop = 10 % de l'ATR 14 jours depuis le prix d'entrée ; pas d'objectif ; sortie à la clôture (16 h).
- taille : risque de RISQUE % du capital par trade, levier total plafonné à 4 (réparti entre les trades du jour).
Coûts : COUT_PCT % du prix aller-retour (courtier sans commission type Alpaca + glissement).
Limite : le volume vient du CFD Dukascopy, pas de la bourse ; c'est une approximation du vrai volume relatif.
Usage : python3 scripts/labo_orb_actions.py sortie.json
"""
import collections, json, os, sys
from datetime import date, timedelta
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import numpy as np, pandas as pd
from bots.donnees_duka import _jour, ENTETES
import lzma, struct, urllib.request
from pathlib import Path

TICKS = Path(__file__).resolve().parent.parent / "data" / "duka_ticks"


def ticks_minute(t, ts):
    """Prix bid tick par tick de la minute ts (UTC), données Dukascopy (mois numérotés à partir de 0)."""
    ts = pd.Timestamp(ts).tz_convert("UTC")
    f = TICKS / t / f"{ts:%Y-%m-%d-%H}.bi5"
    if not f.exists():
        u = f"https://datafeed.dukascopy.com/datafeed/{t}USUSD/{ts.year}/{ts.month - 1:02d}/{ts.day:02d}/{ts.hour:02d}h_ticks.bi5"
        brut = b""
        for _ in range(3):
            try:
                brut = urllib.request.urlopen(urllib.request.Request(u, headers=ENTETES), timeout=30).read()
                break
            except Exception:
                pass
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(brut)
    brut = f.read_bytes()
    if not brut:
        return None
    raw = lzma.decompress(brut)
    debut = ts.minute * 60000
    return [struct.unpack(">IIIff", raw[i:i + 20])[2] / 1000 for i in range(0, len(raw), 20)
            if debut <= struct.unpack(">I", raw[i:i + 4])[0] < debut + 60000]
from telecharger_actions import TICKERS

DEBUT, FIN, COMPTE_DES = date(2025, 11, 17), date(2026, 10, 15), "2026-01-01"
COUT_PCT = 0.03
MODE = os.environ.get("ORB_MODE", "ticks")     # ticks : minute d'entrée tranchée tick par tick ; prudent / optimiste : bornes
NY = "America/New_York"


def seances(t):
    jours = [DEBUT + timedelta(d) for d in range((FIN - DEBUT).days + 1)]
    lignes = []
    for d in jours:
        if d.weekday() >= 5:
            continue
        df = _jour(f"{t}.US-USD", d)
        if not len(df):
            continue
        x = df.tz_convert(NY).between_time("09:30", "15:59")
        if len(x) < 300:
            continue
        orb = x.iloc[:5]
        if orb.index[0].strftime("%H:%M") != "09:30":
            continue
        reste = x.iloc[5:]
        temps = list(reste.index)
        lignes.append({"jour": str(x.index[0].date()), "o": x["open"].iloc[0], "h": x["high"].max(),
                       "l": x["low"].min(), "c": x["close"].iloc[-1],
                       "or_o": orb["open"].iloc[0], "or_c": orb["close"].iloc[-1], "or_h": orb["high"].max(),
                       "or_l": orb["low"].min(), "or_v": orb["volume"].sum(),
                       "chemin": reste[["open", "high", "low", "close"]].to_numpy(), "temps": temps})
    return lignes


def trades_action(t):
    s = seances(t)
    out = []
    for k in range(15, len(s)):
        j = s[k]
        prec = s[k - 14:k]
        tr = [max(p["h"], s[k - 15 + i]["c"]) - min(p["l"], s[k - 15 + i]["c"]) for i, p in enumerate(prec)]
        atr = float(np.mean(tr))
        vol_moy = np.mean([p["or_v"] for p in prec])
        rel = j["or_v"] / vol_moy if vol_moy > 0 else 0
        if j["or_c"] == j["or_o"]:
            continue
        sens = 1 if j["or_c"] > j["or_o"] else -1
        niveau = j["or_h"] if sens == 1 else j["or_l"]
        dist = 0.10 * atr
        entree = stop = t_entree = None
        sortie = motif = None
        ambigu = False
        for (o, h, l, c), ts in zip(j["chemin"], j["temps"]):
            if entree is None:
                if (sens == 1 and h >= niveau) or (sens == -1 and l <= niveau):
                    entree = max(niveau, o) if sens == 1 else min(niveau, o)
                    stop = entree - sens * dist
                    t_entree = str(ts)
                    if (sens == 1 and l <= stop) or (sens == -1 and h >= stop):   # stop dans la minute d'entrée
                        ambigu = True
                        tk = ticks_minute(t, ts) if MODE == "ticks" else None
                        if tk:
                            k0 = next((q for q, px in enumerate(tk) if (px >= niveau if sens == 1 else px <= niveau)), None)
                            touche = k0 is not None and any((px <= stop if sens == 1 else px >= stop) for px in tk[k0:])
                        else:
                            touche = MODE != "optimiste"
                        if touche:
                            sortie, motif = stop, "stop"
                            break
                continue
            if (sens == 1 and l <= stop) or (sens == -1 and h >= stop):
                sortie, motif = (min(stop, o) if sens == 1 else max(stop, o)), "stop"
                break
        if entree is None:
            continue
        if sortie is None:
            sortie, motif = j["chemin"][-1][3], "cloture"
        brut = (sortie - entree) * sens / dist
        cout = COUT_PCT / 100 * entree / dist
        out.append({"actif": t, "jour": j["jour"], "sens": sens, "ambigu": ambigu, "t_entree": t_entree, "rel_vol": round(float(rel), 2),
                    "prix": round(float(j["o"]), 2), "atr": round(atr, 3), "entree": round(float(entree), 3),
                    "sortie": round(float(sortie), 3), "motif": motif, "r_brut": round(float(brut), 3),
                    "r": round(float(brut - cout), 3), "ret_pct": round(float((brut - cout) * dist / entree * 100), 4),
                    "stop_pct": round(float(dist / entree * 100), 4)})
    return out


def portefeuille(tr, top=20, rel_min=1.0, risque=1.0, levier=4.0, capital=10000.0):
    """Simule le compte : chaque jour, les `top` actions au plus fort volume relatif (≥ rel_min) qui déclenchent."""
    par_jour = collections.defaultdict(list)
    for t in tr:
        if t["prix"] > 5 and t["atr"] > 0.5:
            par_jour[t["jour"]].append(t)
    eq, pic, dd = capital, capital, 0.0
    mois, choisis = collections.defaultdict(float), []
    for jour in sorted(par_jour):
        cand = sorted([t for t in par_jour[jour] if t["rel_vol"] >= rel_min], key=lambda t: -t["rel_vol"])[:top]
        if not cand:
            continue
        pnl = 0.0
        for t in cand:
            notion = min(eq * risque / 100 / (t["stop_pct"] / 100), eq * levier / len(cand))
            pnl += notion * t["ret_pct"] / 100
            choisis.append({**t, "risque_reel_pct": round(notion * t["stop_pct"] / 100 / eq * 100, 3)})
        eq += pnl
        if jour >= COMPTE_DES:
            mois[jour[:7]] += pnl
        pic = max(pic, eq); dd = min(dd, (eq - pic) / pic * 100)
    return eq, dd, mois, choisis


def resume(nom, eq0, tr, **kw):
    tr = [t for t in tr if t["jour"] >= "2025-12-01"]
    eq, dd, mois, ch = portefeuille(tr, **kw)
    ch = [t for t in ch if t["jour"] >= COMPTE_DES]
    gain = sum(mois.values())
    r = [t["r"] for t in ch]
    return {"nom": nom, "reglages": kw, "trades": len(ch), "trades_jour": round(len(ch) / max(1, len({t["jour"] for t in ch})), 1),
            "gain_2026": round(gain), "gain_pct": round(gain / eq0 * 100, 1), "pire_baisse_pct": round(dd, 1),
            "r_moyen": round(float(np.mean(r)), 3) if r else None, "r_brut_moyen": round(float(np.mean([t["r_brut"] for t in ch])), 3) if ch else None,
            "reussite": round(sum(x > 0 for x in r) / len(r), 2) if r else None,
            "risque_reel_moyen_pct": round(float(np.mean([t["risque_reel_pct"] for t in ch])), 3) if ch else None,
            "mois_pos": f"{sum(v > 0 for v in mois.values())}/{len(mois)}", "mois": {k: round(v) for k, v in sorted(mois.items())},
            "achats_r": round(float(np.mean([t["r"] for t in ch if t["sens"] == 1])), 3) if ch else None,
            "ventes_r": round(float(np.mean([t["r"] for t in ch if t["sens"] == -1])), 3) if ch else None}


if __name__ == "__main__":
    with ProcessPoolExecutor(2) as ex:
        tous = [t for lst in ex.map(trades_action, TICKERS) for t in lst]
    res = [resume("Stocks in Play : top 20, volume relatif ≥ 100 % (papier)", 10000, tous, top=20, rel_min=1.0),
           resume("Stocks in Play : top 5, volume relatif ≥ 100 %", 10000, tous, top=5, rel_min=1.0),
           resume("Stocks in Play : volume relatif ≥ 200 %, top 20", 10000, tous, top=20, rel_min=2.0),
           resume("Base : toutes les actions, sans filtre de volume", 10000, tous, top=1000, rel_min=0.0),
           resume("Stocks in Play top 20, risque 0,5 % (règle de Rayan)", 10000, tous, top=20, rel_min=1.0, risque=0.5)]
    for r in res:
        print(json.dumps({k: v for k, v in r.items() if k != "mois"}, ensure_ascii=False))
        print("   mois", r["mois"])
    json.dump({"resultats": res, "trades": tous}, open(sys.argv[1], "w"), ensure_ascii=False, default=str)
    print("FIN")
