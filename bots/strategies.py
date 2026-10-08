"""Les trois stratégies. Chacune renvoie un signal (dict) ou None à la clôture de la bougie i.

Signal : sens (1 achat, -1 vente), entree, stop, cible, setup, ordre ('limite' ou 'marche'),
expiration (en bougies 1 min, pour un ordre limite).
"""
from datetime import timedelta

NY_DEBUT, NY_FIN = 15 * 60 + 30, 17 * 60 + 30     # heure de Paris
LONDRES_DEBUT, LONDRES_FIN = 8 * 60, 11 * 60


def _signal(sens, entree, stop, cible, setup, r_min, ordre="marche", expiration=0):
    risque = (entree - stop) * sens
    if risque <= 0:
        return None
    r = (cible - entree) * sens / risque
    if r < r_min:
        return None
    return {"sens": sens, "entree": float(entree), "stop": float(stop), "cible": float(cible),
            "setup": setup, "ordre": ordre, "expiration": expiration, "r_prevu": round(r, 2)}


# ---------------------------------------------------------------- 1. ICT
def ict(m, i, p):
    if i < p["fenetre_sweep"] + 5:
        return None
    pm = m.paris_min[i]
    dans_ny = NY_DEBUT <= pm < NY_FIN
    dans_londres = m.type == "crypto" and LONDRES_DEBUT <= pm < LONDRES_FIN
    if not (dans_ny or dans_londres):
        return None
    d = i - 1                                   # bougie de déplacement ; i ferme le FVG
    if m.h[d] - m.l[d] < p["deplacement_atr"] * m.atr1[d]:
        return None
    biais = m.biais(i)
    if biais == 0:
        return None
    hauts, bas = m.liquidite(i)
    debut = max(0, d - p["fenetre_sweep"])

    if biais == 1 and m.c[d] > m.o[d] and m.l[i] > m.h[i - 2]:
        sh = m.dernier_sh1[d - 1]
        if sh != sh or m.c[d] <= sh:            # pas de changement de structure
            return None
        balaye = None
        for s in range(d - 1, debut - 1, -1):
            if any(m.l[s] < L < m.c[s] for L in bas):
                balaye = s
                break
        if balaye is None:
            return None
        stop = m.l[balaye:i + 1].min() - p["marge_stop_atr"] * m.atr1[i]
        entree = (m.l[i] + m.h[i - 2]) / 2
        cibles = sorted(x for x in hauts if x > entree)
        if not cibles:
            return None
        return _signal(1, entree, stop, cibles[0], "sweep_fvg", p["r_min"], "limite", p["expiration_bougies"])

    if biais == -1 and m.c[d] < m.o[d] and m.h[i] < m.l[i - 2]:
        sl = m.dernier_sl1[d - 1]
        if sl != sl or m.c[d] >= sl:
            return None
        balaye = None
        for s in range(d - 1, debut - 1, -1):
            if any(m.h[s] > L > m.c[s] for L in hauts):
                balaye = s
                break
        if balaye is None:
            return None
        stop = m.h[balaye:i + 1].max() + p["marge_stop_atr"] * m.atr1[i]
        entree = (m.h[i] + m.l[i - 2]) / 2
        cibles = sorted((x for x in bas if x < entree), reverse=True)
        if not cibles:
            return None
        return _signal(-1, entree, stop, cibles[0], "sweep_fvg", p["r_min"], "limite", p["expiration_bougies"])
    return None


# ---------------------------------------------------------------- 2. Support / résistance
def _zones(prix, tol, touches_min):
    zones, groupe = [], []
    for x in sorted(prix):
        if groupe and x - groupe[-1] > tol:
            if len(groupe) >= touches_min:
                zones.append((groupe[0], groupe[-1]))
            groupe = []
        groupe.append(x)
    if len(groupe) >= touches_min:
        zones.append((groupe[0], groupe[-1]))
    return zones


def _meches(o, h, l, c):
    rng = h - l
    if rng <= 0:
        return 0.0, 0.0
    return (min(o, c) - l) / rng, (h - max(o, c)) / rng


def sr(m, i, p):
    if not m.fin_bougie5(i):
        return None
    b = m.n5[i] - 1
    n15 = m.n15[i]
    if b < p["fenetre_retest"] + 2 or n15 < 20:
        return None
    if m.type == "action" and not (NY_DEBUT <= m.paris_min[i] < 21 * 60 + 45):
        return None
    hauts, bas = m.swings15(i, m.t[i] - timedelta(days=p["jours_zones"]))
    zones = _zones(hauts + bas, p["regroupement_atr"] * m.atr15[n15 - 1], p["touches_min"])
    if not zones:
        return None
    o, h, l, c = m.o5[b], m.h5[b], m.l5[b], m.c5[b]
    a5 = m.atr5[b]
    m_bas, m_haut = _meches(o, h, l, c)
    marge = p["marge_stop_atr"] * a5

    for bas_z, haut_z in sorted(zones, key=lambda z: abs((z[0] + z[1]) / 2 - c)):
        dessus = [z for z in zones if z[0] > c]
        dessous = [z for z in zones if z[1] < c]
        # Achat : rejet par le haut de la zone
        if l <= haut_z < c and m_bas >= p["meche_min"]:
            cible = min(z[0] for z in dessus) if dessus else None
            if cible is None:
                continue
            setup = "rebond"
            for k in range(b - 1, b - p["fenetre_retest"] - 1, -1):
                if m.c5[k] > haut_z and m.c5[k - 1] <= haut_z and all(m.c5[k:b] > haut_z):
                    setup = "cassure_retest"
                    break
            if setup == "rebond" and m.c5[b - 1] <= haut_z:
                continue                       # le rebond suppose d'arriver par le dessus
            s = _signal(1, c, bas_z - marge, cible, setup, p["r_min"])
            if s:
                return s
        # Vente : rejet par le bas de la zone
        if c < bas_z <= h and m_haut >= p["meche_min"]:
            cible = max(z[1] for z in dessous) if dessous else None
            if cible is None:
                continue
            setup = "rebond"
            for k in range(b - 1, b - p["fenetre_retest"] - 1, -1):
                if m.c5[k] < bas_z and m.c5[k - 1] >= bas_z and all(m.c5[k:b] < bas_z):
                    setup = "cassure_retest"
                    break
            if setup == "rebond" and m.c5[b - 1] >= bas_z:
                continue
            s = _signal(-1, c, haut_z + marge, cible, setup, p["r_min"])
            if s:
                return s
    return None


# ---------------------------------------------------------------- 3. Volume Profile
def vp(m, i, p):
    if not m.fin_bougie5(i):
        return None
    if m.type == "action" and not (NY_DEBUT <= m.paris_min[i] < 21 * 60 + 45):
        return None
    prof = m.profil_precedent(i)
    if not prof:
        return None
    poc, vah, val = prof["poc"], prof["vah"], prof["val"]
    b = m.n5[i] - 1
    o, h, l, c = m.o5[b], m.h5[b], m.l5[b], m.c5[b]
    marge = p["marge_stop_atr"] * m.atr5[b]
    m_bas, m_haut = _meches(o, h, l, c)

    # Règle des 80 %
    ouverture = m.o[m.debut_session[i]]
    if ouverture < val or ouverture > vah:
        sess = m.session[i]
        n15 = m.n15[i]
        idx15 = [j for j in range(max(0, n15 - 120), n15) if m.session15[j] == sess]
        arme_a = None
        for a, bb in zip(idx15, idx15[1:]):
            if all(val <= m.c15[x] <= vah for x in (a, bb)):
                arme_a = m.fin15[bb]
                break
        if arme_a is not None:
            debut5 = int((m.fin5 <= arme_a).sum())
            sens = 1 if ouverture < val else -1
            # premier repli : une bougie contre le sens puis une bougie dans le sens
            premier = None
            for x in range(max(debut5, 1), b + 1):
                contre = (m.c5[x - 1] - m.o5[x - 1]) * sens < 0
                avec = (m.c5[x] - m.o5[x]) * sens > 0
                if contre and avec:
                    premier = x
                    break
            if premier == b:
                stop = (val - marge) if sens == 1 else (vah + marge)
                cible = vah if sens == 1 else val
                s = _signal(sens, c, stop, cible, "regle_80", p["r_min"])
                if s:
                    return s

    # Rejet de bord de la zone de valeur
    prec_dedans = val <= m.c5[b - 1] <= vah
    if prec_dedans and h >= vah > c and m_haut >= p["meche_min"]:
        s = _signal(-1, c, max(h, vah) + marge, poc, "rejet_vah", p["r_min"])
        if s:
            return s
    if prec_dedans and l <= val < c and m_bas >= p["meche_min"]:
        s = _signal(1, c, min(l, val) - marge, poc, "rejet_val", p["r_min"])
        if s:
            return s
    return None


STRATEGIES = {"ict": ict, "sr": sr, "vp": vp}
NOMS = {"ict": "ICT", "sr": "Support / résistance", "vp": "Volume Profile"}
