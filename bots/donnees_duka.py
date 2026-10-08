"""Bougies 1 min historiques gratuites (Dukascopy) pour les marchés IG : Nasdaq 100, or, EUR/USD, Bitcoin.

Les prix Dukascopy sont très proches de ceux d'IG (mêmes sous-jacents au comptant) ; les coûts IG
(écart achat/vente) sont ajoutés à part dans le backtest. Seuls les jours terminés sont disponibles.

L'ancien test échouait parce que le serveur répond vide (HTTP 202) sans en-têtes de navigateur.
Les jours téléchargés sont gardés dans data/duka/ pour ne pas les retélécharger.
"""
import gzip
import io
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from itertools import accumulate
from pathlib import Path

import pandas as pd

URL = "https://jetta.dukascopy.com/v1/candles/minute/{inst}/{cote}/{a}/{m}/{j}"
ENTETES = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36",
           "Referer": "https://freeserv.dukascopy.com/", "Accept": "application/json"}
CACHE = Path(__file__).resolve().parent.parent / "data" / "duka"
COLS = ["open", "high", "low", "close", "volume"]


def _jour(inst, d, cote="BID", essais=4):
    """Bougies d'un jour (UTC). DataFrame vide si marché fermé (week-end, férié)."""
    f = CACHE / inst / f"{d:%Y-%m-%d}.csv.gz"
    if f.exists():
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    url = URL.format(inst=inst, cote=cote, a=d.year, m=d.month, j=d.day)
    brut = None
    for k in range(essais):
        try:
            req = urllib.request.Request(url, headers=ENTETES)
            with urllib.request.urlopen(req, timeout=30) as r:
                txt = r.read()
                if r.status == 200 and txt:
                    brut = json.loads(txt)
                    break
        except urllib.error.HTTPError as e:
            if e.code == 400:      # jour pas encore terminé
                return pd.DataFrame(columns=COLS)
        except Exception:
            pass
        time.sleep(1.5 * (k + 1))
    if not brut or "times" not in brut:
        return pd.DataFrame(columns=COLS)
    mult = brut["multiplier"]
    t = [brut["timestamp"] + x * brut["shift"] for x in accumulate(brut["times"])]
    df = pd.DataFrame({
        "open": [brut["open"] + x * mult for x in accumulate(brut["opens"])],
        "high": [brut["high"] + x * mult for x in accumulate(brut["highs"])],
        "low": [brut["low"] + x * mult for x in accumulate(brut["lows"])],
        "close": [brut["close"] + x * mult for x in accumulate(brut["closes"])],
        "volume": brut["volumes"],
    }, index=pd.to_datetime(t, unit="ms", utc=True))
    df = df.round(6)
    # Dukascopy remplit parfois les minutes sans cotation avec des bougies plates et sans volume
    df = df[~((df["volume"] <= 0) & (df["high"] == df["low"]))]
    if d < datetime.now(timezone.utc).date():       # jour terminé : on le garde
        f.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(f, "wt") as g:
            df.to_csv(g)
    return df


def historique(inst, jours, fin=None, travailleurs=8):
    fin = fin or (datetime.now(timezone.utc).date() - timedelta(days=1))
    dates = [fin - timedelta(days=k) for k in range(jours)][::-1]
    with ThreadPoolExecutor(travailleurs) as ex:
        morceaux = list(ex.map(lambda d: _jour(inst, d), dates))
    morceaux = [m for m in morceaux if len(m)]
    if not morceaux:
        return pd.DataFrame(columns=COLS)
    df = pd.concat(morceaux).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df[COLS].astype(float)


if __name__ == "__main__":
    import sys
    inst = sys.argv[1] if len(sys.argv) > 1 else "XAU-USD"
    j = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    df = historique(inst, j)
    print(inst, len(df), "bougies", df.index.min(), "->", df.index.max())
    print(df.tail(3))
