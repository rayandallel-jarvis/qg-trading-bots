"""Récupération des bougies 1 min chez Alpaca (actions : flux IEX gratuit, séance régulière seulement)."""
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

COLS = ["open", "high", "low", "close", "volume"]


class Donnees:
    def __init__(self):
        cle, secret = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
        self.actions = StockHistoricalDataClient(cle, secret)
        self.crypto = CryptoHistoricalDataClient(cle, secret)

    def barres(self, symbole, type_actif, debut, fin=None):
        if type_actif == "crypto":
            req = CryptoBarsRequest(symbol_or_symbols=symbole, timeframe=TimeFrame.Minute, start=debut, end=fin)
            df = self.crypto.get_crypto_bars(req).df
        else:
            req = StockBarsRequest(symbol_or_symbols=symbole, timeframe=TimeFrame.Minute, start=debut, end=fin,
                                   feed=DataFeed.IEX)
            df = self.actions.get_stock_bars(req).df
        if df is None or df.empty:
            return pd.DataFrame(columns=COLS)
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbole, level=0)
        df = df[COLS].astype(float).sort_index()
        df.index = pd.to_datetime(df.index, utc=True)
        if type_actif == "action":
            ny = df.index.tz_convert("America/New_York")
            minutes = ny.hour * 60 + ny.minute
            df = df[(minutes >= 9 * 60 + 30) & (minutes < 16 * 60) & (ny.dayofweek < 5)]
        # on ne garde que des bougies terminées
        limite = pd.Timestamp(datetime.now(timezone.utc)).floor("min") - pd.Timedelta(minutes=1)
        return df[df.index <= limite]

    def historique(self, symbole, type_actif, jours):
        debut = datetime.now(timezone.utc) - timedelta(days=jours)
        return self.barres(symbole, type_actif, debut)
