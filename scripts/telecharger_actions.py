"""Télécharge les bougies 1 min Dukascopy des actions US (jours ouvrés) dans data/duka/<TICKER>.US-USD/."""
import sys, os, time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from bots.donnees_duka import _jour
TICKERS = ("AAPL MSFT NVDA AMZN GOOGL TSLA AMD AVGO NFLX ADBE CRM ORCL INTC QCOM MU TXN AMAT LRCX MRVL PYPL UBER SNOW "
           "PLTR BABA BIDU DIS NKE SBUX MCD KO PEP WMT COST TGT HD LOW BA CAT DE GE GM XOM CVX OXY SLB JPM BAC C WFC GS "
           "MS V MA AXP UNH LLY JNJ PFE MRK MRNA CVS T VZ TMUS IBM CSCO DELL HPQ ZM DOCU ROKU SPOT SQ PANW ZS OKTA TEAM "
           "MDB TTD ENPH DAL UAL CCL RCL ABT TMO").split()
if __name__ == "__main__":
    debut, fin = date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2])
    jours = [debut + timedelta(d) for d in range((fin - debut).days + 1)]
    jours = [d for d in jours if d.weekday() < 5]
    t0 = time.time()
    for k, t in enumerate(TICKERS):
        with ThreadPoolExecutor(24) as ex:
            n = sum(len(x) > 0 for x in ex.map(lambda d: _jour(f"{t}.US-USD", d), jours))
        print(f"{k + 1}/{len(TICKERS)} {t} {n} jours ({time.time() - t0:.0f} s)", flush=True)
    print("FIN")
