"""Les trois stratégies. Chacune renvoie un signal (dict) ou None à la clôture de la bougie i.

Signal : sens (1 achat, -1 vente), entree, stop, cible, setup, ordre ('limite' ou 'marche'),
expiration (en bougies 1 min, pour un ordre limite).
"""
from datetime import timedelta

import numpy as np

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
            "setup": setup, "ordre": ordre, "expiration": expiration, "r_prevu": round(float(r), 2)}


# ---------------------------------------------------------------- 1. ICT (modèle de Rayan, 8 oct.)
# Tendance 1 h (structure) · jambe 1 h depuis le dernier swing opposé jusqu'à l'extrême
# · zone = order block 1 h ∩ OTE (62–79 %) · le prix touche la zone · MSS 5 min
# (clôture au-delà du dernier swing 5 min avant l'extrême de la correction) · entrée au marché
# · stop sur l'extrême de la correction · objectif : liquidité 1 h (swing) à au moins 2R,
# sinon la suivante · break-even à 1R.
def ict(m, i, p):
    if not m.fin_bougie5(i):
        return None
    n60 = m.n60[i]
    if n60 < 30:
        return None
    s = m.s60
    sens = int(s["tendance"][n60 - 1])
    if sens == 0:
        return None
    # dernière cassure (BOS) dans le sens de la tendance, sur les bougies 1 h terminées
    k = int(np.searchsorted(m.bos60_m, n60 - 1, side="right")) - 1
    while k >= 0 and s["bos"][k][1] != sens:
        k -= 1
    if k < 0:
        return None
    mb, _, _, i_origine = s["bos"][k]
    if i_origine < 0 or n60 - 1 - mb > p["age_max_bos_h"]:
        return None
    h60, l60, o60, c60 = m.h60, m.l60, m.o60, m.c60
    if sens == -1:
        haut = h60[i_origine]                         # départ de la jambe baissière
        bas = l60[i_origine:n60].min()
        # order block : dernière bougie haussière avant la bougie de cassure
        ob = next((j for j in range(mb - 1, i_origine - 1, -1) if c60[j] > o60[j]), None)
        if ob is None:
            return None
        ote_bas, ote_haut = bas + p["ote_min"] * (haut - bas), bas + p["ote_max"] * (haut - bas)
    else:
        bas = l60[i_origine]
        haut = h60[i_origine:n60].max()
        ob = next((j for j in range(mb - 1, i_origine - 1, -1) if c60[j] < o60[j]), None)
        if ob is None:
            return None
        ote_bas, ote_haut = haut - p["ote_max"] * (haut - bas), haut - p["ote_min"] * (haut - bas)
    z_bas, z_haut = max(l60[ob], ote_bas), min(h60[ob], ote_haut)
    if z_bas >= z_haut:
        return None                                   # pas de chevauchement OB / OTE

    # bougies 5 min depuis la clôture de la bougie de cassure
    b = m.n5[i] - 1
    debut = int(np.searchsorted(m.fin5, m.fin60[mb], side="left"))
    if debut >= b:
        return None
    h5, l5, c5 = m.h5, m.l5, m.c5
    if sens == -1:
        touche = next((x for x in range(debut, b) if h5[x] >= z_bas), None)
        if touche is None or h5[touche:b + 1].max() > haut:
            return None                               # pas encore dans la zone, ou jambe invalidée
        xi = touche + int(np.argmax(h5[touche:b]))    # extrême de la correction (avant la bougie b)
        extreme = h5[xi]
        swings5 = [j for j in m.idx_sl5 if j < xi and j + m.k5 <= b]
        if not swings5:
            return None
        niveau = l5[swings5[-1]]
        if not (c5[b] < niveau <= c5[b - 1]):
            return None
    else:
        touche = next((x for x in range(debut, b) if l5[x] <= z_haut), None)
        if touche is None or l5[touche:b + 1].min() < bas:
            return None
        xi = touche + int(np.argmin(l5[touche:b]))
        extreme = l5[xi]
        swings5 = [j for j in m.idx_sh5 if j < xi and j + m.k5 <= b]
        if not swings5:
            return None
        niveau = h5[swings5[-1]]
        if not (c5[b] > niveau >= c5[b - 1]):
            return None
    if b - touche > p["attente_mss_bougies5"]:
        return None

    entree = c5[b]
    stop = extreme + sens * p["marge_stop_atr"] * m.atr5[b]
    risque = (entree - stop) * sens
    if risque <= 0:
        return None
    # liquidité 1 h : bas (ou hauts) des swings confirmés, du plus proche au plus lointain
    lim = n60 - 1 - m.s60_k
    if sens == -1:
        niveaux = sorted({float(l60[j]) for j in np.where(s["sl"])[0] if j <= lim and l60[j] < entree}
                         | {float(bas)}, reverse=True)
        niveaux = [x for x in niveaux if x < entree]
    else:
        niveaux = sorted({float(h60[j]) for j in np.where(s["sh"])[0] if j <= lim and h60[j] > entree}
                         | {float(haut)})
        niveaux = [x for x in niveaux if x > entree]
    cible = next((x for x in niveaux if (x - entree) * sens / risque >= p["r_min"]), None)
    if cible is None:
        return None
    sig = _signal(sens, entree, stop, cible, "ob_ote_mss", p["r_min"])
    if sig:
        sig["be_r"] = p.get("break_even_r")
        sig["zone"] = [round(float(z_bas), 4), round(float(z_haut), 4)]
    return sig


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
