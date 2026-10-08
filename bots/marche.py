"""Préparation des données : bougies 1/5/15 min, ATR, swings, biais, niveaux de liquidité.

Tout est calculé une fois sur la fenêtre de données ; les stratégies n'utilisent
à l'indice i que des informations disponibles à la clôture de la bougie i.
"""
from datetime import timedelta

import numpy as np
import pandas as pd

UNE_MIN = pd.Timedelta(minutes=1)


def atr(df, n=14):
    pc = df["close"].shift()
    tr = np.maximum(df["high"] - df["low"],
                    np.maximum((df["high"] - pc).abs(), (df["low"] - pc).abs()))
    return tr.rolling(n, min_periods=1).mean().to_numpy()


def swings(h, l, k):
    """Swing haut à j : plus haut que les k bougies de chaque côté (idem bas)."""
    hs, ls = pd.Series(h), pd.Series(l)
    g_h = hs.rolling(k).max().shift(1)
    d_h = hs[::-1].rolling(k).max().shift(1)[::-1]
    g_l = ls.rolling(k).min().shift(1)
    d_l = ls[::-1].rolling(k).min().shift(1)[::-1]
    sh = ((hs > g_h) & (hs > d_h)).to_numpy()
    sl = ((ls < g_l) & (ls < d_l)).to_numpy()
    return sh, sl


def derniers_confirmes(valeurs, masque, k):
    """Valeur du dernier swing confirmé (à j+k) disponible à chaque indice."""
    n = len(valeurs)
    out = np.full(n, np.nan)
    for j in np.where(masque)[0]:
        if j + k < n:
            out[j + k] = valeurs[j]
    return pd.Series(out).ffill().to_numpy()


def reechantillonner(df, regle):
    return df.resample(regle, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()


def profil_volume(h, l, c, v, part=0.70, nb=120):
    """POC, VAH, VAL d'un ensemble de bougies."""
    bas, haut = float(np.min(l)), float(np.max(h))
    if haut <= bas or np.sum(v) <= 0:
        return None
    tp = (h + l + c) / 3
    idx = np.clip(((tp - bas) / (haut - bas) * nb).astype(int), 0, nb - 1)
    vol = np.bincount(idx, weights=v, minlength=nb)
    bords = np.linspace(bas, haut, nb + 1)
    poc = int(np.argmax(vol))
    total, acc, i_bas, i_haut = vol.sum(), vol[poc], poc, poc
    while acc < part * total:
        up = vol[i_haut + 1] if i_haut + 1 < nb else -1
        dn = vol[i_bas - 1] if i_bas > 0 else -1
        if up < 0 and dn < 0:
            break
        if up >= dn:
            i_haut += 1
            acc += up
        else:
            i_bas -= 1
            acc += dn
    return {"poc": (bords[poc] + bords[poc + 1]) / 2, "vah": bords[i_haut + 1], "val": bords[i_bas]}


class Marche:
    def __init__(self, df, type_actif, k=3, part_valeur=0.70):
        self.type = type_actif
        self.k = k
        self.part_valeur = part_valeur
        self.df = df
        self.t = df.index
        self.o = df["open"].to_numpy()
        self.h = df["high"].to_numpy()
        self.l = df["low"].to_numpy()
        self.c = df["close"].to_numpy()
        self.v = df["volume"].to_numpy()
        self.n = len(df)
        self.atr1 = atr(df)
        self.fin1 = self.t + UNE_MIN
        paris = self.fin1.tz_convert("Europe/Paris")
        self.paris_min = (paris.hour * 60 + paris.minute).to_numpy()
        self.paris_jour = np.array([str(d) for d in paris.date])
        self.heure_utc = self.t.hour.to_numpy()

        # Sessions : jour de New York pour les actions, jour UTC pour la crypto
        if type_actif == "action":
            jours = self.t.tz_convert("America/New_York").date
        else:
            jours = self.t.date
        self.session = np.array([str(d) for d in jours])

        # Swings 1 min
        sh, sl = swings(self.h, self.l, k)
        self.dernier_sh1 = derniers_confirmes(self.h, sh, k)
        self.dernier_sl1 = derniers_confirmes(self.l, sl, k)

        # 5 et 15 min
        self.d5 = reechantillonner(df, "5min")
        self.d15 = reechantillonner(df, "15min")
        fin5 = (self.d5.index + pd.Timedelta(minutes=5)).to_numpy()
        fin15 = (self.d15.index + pd.Timedelta(minutes=15)).to_numpy()
        self.n5 = np.searchsorted(fin5, self.fin1.to_numpy(), side="right")
        self.n15 = np.searchsorted(fin15, self.fin1.to_numpy(), side="right")
        self.fin15 = fin15
        self.fin5 = fin5
        self.o5, self.h5 = self.d5["open"].to_numpy(), self.d5["high"].to_numpy()
        self.l5, self.c5 = self.d5["low"].to_numpy(), self.d5["close"].to_numpy()
        self.atr5 = atr(self.d5)
        self.c15 = self.d15["close"].to_numpy()
        self.h15, self.l15 = self.d15["high"].to_numpy(), self.d15["low"].to_numpy()
        self.atr15 = atr(self.d15)
        sh15, sl15 = swings(self.h15, self.l15, k)
        self.idx_sh15 = np.where(sh15)[0]
        self.idx_sl15 = np.where(sl15)[0]
        self.biais15 = self._biais(sh15, sl15)
        self.session15 = np.array([str(d) for d in (
            self.d15.index.tz_convert("America/New_York").date if type_actif == "action"
            else self.d15.index.date)])

        # Plus haut / plus bas de la session précédente
        ser = pd.DataFrame({"s": self.session, "h": self.h, "l": self.l})
        g = ser.groupby("s").agg(h=("h", "max"), l=("l", "min")).sort_index()
        prec = g.shift(1)
        self.prec_h = prec["h"].reindex(self.session).to_numpy()
        self.prec_l = prec["l"].reindex(self.session).to_numpy()
        self._sessions = list(g.index)

        # Session asiatique (crypto) : 00 h – 07 h UTC du jour
        self.asie_h = np.full(self.n, np.nan)
        self.asie_l = np.full(self.n, np.nan)
        if type_actif == "crypto":
            m = self.heure_utc < 7
            a = pd.DataFrame({"s": self.session[m], "h": self.h[m], "l": self.l[m]}).groupby("s").agg(
                h=("h", "max"), l=("l", "min"))
            ok = self.heure_utc >= 7
            self.asie_h[ok] = a["h"].reindex(self.session[ok]).to_numpy()
            self.asie_l[ok] = a["l"].reindex(self.session[ok]).to_numpy()

        # Ouverture de session et profils de volume (cache)
        premier = pd.Series(np.arange(self.n)).groupby(self.session).transform("min").to_numpy()
        self.debut_session = premier
        self._profils = {}

    # ------------------------------------------------------------------ helpers
    def _biais(self, sh, sl):
        n = len(self.c15)
        out = np.zeros(n, dtype=int)
        dsh = derniers_confirmes(self.h15, sh, self.k)
        dsl = derniers_confirmes(self.l15, sl, self.k)
        etat = 0
        for m in range(n):
            if not np.isnan(dsh[m]) and self.c15[m] > dsh[m]:
                etat = 1
            elif not np.isnan(dsl[m]) and self.c15[m] < dsl[m]:
                etat = -1
            out[m] = etat
        return out

    def biais(self, i):
        n = self.n15[i]
        return int(self.biais15[n - 1]) if n > 0 else 0

    def swings15(self, i, depuis):
        """Prix des swings 15 min confirmés à l'indice i et apparus après `depuis`."""
        n = self.n15[i]
        lim = n - 1 - self.k
        hauts = [self.h15[j] for j in self.idx_sh15 if j <= lim and self.d15.index[j] >= depuis]
        bas = [self.l15[j] for j in self.idx_sl15 if j <= lim and self.d15.index[j] >= depuis]
        return hauts, bas

    def liquidite(self, i):
        hauts, bas = [self.prec_h[i]], [self.prec_l[i]]
        if self.type == "crypto":
            hauts.append(self.asie_h[i])
            bas.append(self.asie_l[i])
        h15, b15 = self.swings15(i, self.t[i] - timedelta(days=1))
        hauts += h15
        bas += b15
        return ([x for x in hauts if x == x], [x for x in bas if x == x])

    def fin_bougie5(self, i):
        return i > 0 and self.n5[i] > self.n5[i - 1]

    def profil_precedent(self, i):
        s = self.session[i]
        if s in self._profils:
            return self._profils[s]
        try:
            pos = self._sessions.index(s)
        except ValueError:
            return None
        res = None
        if pos > 0:
            m = self.session == self._sessions[pos - 1]
            if m.sum() > 30:
                res = profil_volume(self.h[m], self.l[m], self.c[m], self.v[m], self.part_valeur)
        self._profils[s] = res
        return res
