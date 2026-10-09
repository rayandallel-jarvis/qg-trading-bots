"""Les trois stratégies. Chacune renvoie un signal (dict) ou None à la clôture de la bougie i.

Signal : sens (1 achat, -1 vente), entree, stop, cible, setup, ordre ('limite' ou 'marche'),
expiration (en bougies 1 min, pour un ordre limite).
"""
from datetime import timedelta

import numpy as np
from collections import Counter, defaultdict

DIAG = defaultdict(Counter)   # entonnoir : où les setups s'arrêtent (backtest)


COURANT = "?"                 # stratégie en cours d'évaluation (pour l'entonnoir)


def _non(raison):
    DIAG[COURANT][raison] += 1
    return None

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


# ---------------------------------------------------------------- 1. ICT strict (règles canoniques)
# Biais HTF (structure 4 h) · killzones Londres 8 h–11 h (crypto) et New York 15 h 30–17 h 30
# · sweep d'une liquidité (plus haut/bas de la veille, session asiatique, swings 15 min)
# · MSS 1 min avec bougie de déplacement ≥ 1,5 ATR · entrée limite au milieu du FVG
# · stop au-delà de l'extrême du sweep · objectif : liquidité opposée à ≥ 2R.
def ict(m, i, p):
    if i < p["fenetre_sweep"] + 5:
        return None
    pm = m.paris_min[i]
    if not ((NY_DEBUT <= pm < NY_FIN) or (m.type != "action" and LONDRES_DEBUT <= pm < LONDRES_FIN)):
        return _non("hors killzone")
    d = i - 1
    if m.h[d] - m.l[d] < p["deplacement_atr"] * m.atr1[d]:
        return _non("pas de déplacement")
    u = m.ut("4h", p.get("k_htf", 2))
    n = u["n"][i]
    biais = int(u["st"]["tendance"][n - 1]) if n > 0 else 0
    if biais == 0:
        return _non("pas de biais 4h")
    hauts, bas = m.liquidite(i)
    debut = max(0, d - p["fenetre_sweep"])
    if biais == 1 and m.c[d] > m.o[d] and m.l[i] > m.h[i - 2]:
        sh = m.dernier_sh1[d - 1]
        if sh != sh or m.c[d] <= sh:
            return _non("pas de MSS")
        s = next((s for s in range(d - 1, debut - 1, -1) if any(m.l[s] < L < m.c[s] for L in bas)), None)
        if s is None:
            return _non("pas de sweep")
        stop = m.l[s:i + 1].min() - p["marge_stop_atr"] * m.atr1[i]
        entree = (m.l[i] + m.h[i - 2]) / 2
        cibles = sorted(x for x in hauts if x > entree)
        risque = entree - stop
        cible = next((x for x in cibles if risque > 0 and (x - entree) / risque >= p["r_min"]), None)
        if cible is None:
            return _non("pas de liquidité à 2R")
        return _signal(1, entree, stop, cible, "sweep_fvg", p["r_min"], "limite", p["expiration_bougies"])
    if biais == -1 and m.c[d] < m.o[d] and m.h[i] < m.l[i - 2]:
        sl = m.dernier_sl1[d - 1]
        if sl != sl or m.c[d] >= sl:
            return _non("pas de MSS")
        s = next((s for s in range(d - 1, debut - 1, -1) if any(m.h[s] > L > m.c[s] for L in hauts)), None)
        if s is None:
            return _non("pas de sweep")
        stop = m.h[s:i + 1].max() + p["marge_stop_atr"] * m.atr1[i]
        entree = (m.h[i] + m.l[i - 2]) / 2
        cibles = sorted((x for x in bas if x < entree), reverse=True)
        risque = stop - entree
        cible = next((x for x in cibles if risque > 0 and (entree - x) / risque >= p["r_min"]), None)
        if cible is None:
            return _non("pas de liquidité à 2R")
        return _signal(-1, entree, stop, cible, "sweep_fvg", p["r_min"], "limite", p["expiration_bougies"])
    return None


# ---------------------------------------------------------------- 2. Perso (modèle de Rayan, revu le 9 oct.)
# OB sur l'unité de structure (1 h ou 2 h) = dernière bougie opposée avant la clôture qui casse le dernier
# plus bas (vente) / plus haut (achat) : critère n° 1, obligatoire.
# Score de l'OB (1 à 5) : 1 de base, +1 s'il libère un FVG, +1 par unité supérieure où il est aussi
# visible (2 h, 4 h, daily ; « visible » = la bougie de cette unité qui contient l'OB est de la même
# couleur et une des 3 bougies suivantes clôture au-delà de son extrême). Le score ne filtre pas : il classe.
# Zone = OB ∩ OTE (62–79 % de la jambe) · invalidation : clôture de l'unité de structure au-delà de l'OB.
# Après contact : MSS 3 min, ordre limite à 61,8 % de la jambe 3 min · stop au-delà de l'extrême 3 min.
# Objectif : R fixe (2R), plus de visée de liquidité · break-even optionnel.
def _visible(m, i, regle, t_ob, sens):
    u = m.ut(regle, 2)
    q = int(np.searchsorted(u["d"].index, t_ob, side="right")) - 1
    n = u["n"][i]
    if q < 0 or q >= n:
        return False
    O, C, H, L = u["o"], u["c"], u["h"], u["l"]
    if (C[q] > O[q]) != (sens == -1):          # OB de vente = bougie haussière, et inversement
        return False
    suite = C[q + 1:min(q + 4, n)]
    return bool((suite < L[q]).any() if sens == -1 else (suite > H[q]).any())


def perso(m, i, p):
    u3 = m.ut("3min", p["k_3m"])
    if not (i > 0 and u3["n"][i] > u3["n"][i - 1]):
        return None
    b = u3["n"][i] - 1
    us = m.ut(p["ut"], p["k_structure"])
    ns = us["n"][i]
    if ns < 20:
        return _non("historique")
    st = us["st"]
    sens = int(st["tendance"][ns - 1])
    if sens == 0:
        return _non("pas de tendance")
    k = int(np.searchsorted(us["bos_m"], ns - 1, side="right")) - 1
    while k >= 0 and st["bos"][k][1] != sens:
        k -= 1
    if k < 0:
        return _non("pas de BOS")
    mb = st["bos"][k][0]
    if (ns - 1 - mb) > p["age_max_bos"]:
        return _non("BOS trop ancien")
    H, L, O, C = us["h"], us["l"], us["o"], us["c"]
    # OB = dernière bougie opposée avant (ou égale à) la bougie de cassure
    opp = (C > O) if sens == -1 else (C < O)
    j = next((x for x in range(mb, max(mb - 30, -1), -1) if opp[x]), None)
    if j is None:
        return _non("pas d'OB")
    if sens == -1:
        haut = H[j:mb + 1].max(); bas = L[j:ns].min(); i_ext = j + int(np.argmin(L[j:ns]))
        ote = (bas + p["ote_min"] * (haut - bas), bas + p["ote_max"] * (haut - bas))
    else:
        bas = L[j:mb + 1].min(); haut = H[j:ns].max(); i_ext = j + int(np.argmax(H[j:ns]))
        ote = (haut - p["ote_max"] * (haut - bas), haut - p["ote_min"] * (haut - bas))
    z_bas, z_haut = max(L[j], ote[0]), min(H[j], ote[1])
    if z_bas >= z_haut:
        return _non("OB hors OTE")
    limite = H[j] if sens == -1 else L[j]
    apres = C[mb + 1:ns]
    if len(apres) and ((apres > limite).any() if sens == -1 else (apres < limite).any()):
        return _non("zone invalidée")

    # score de l'OB
    fvg = any((L[x - 1] > H[x + 1]) if sens == -1 else (H[x - 1] < L[x + 1])
              for x in range(j + 1, min(mb + 2, ns - 1)))
    t_ob = us["d"].index[j]
    sup = [r for r in ("2h", "4h", "1D") if r != p["ut"] and not (p["ut"] == "2h" and r == "2h")]
    htf = [r for r in sup if _visible(m, i, r, t_ob, sens)]
    score = 1 + int(fvg) + len(htf)

    h3, l3, c3 = u3["h"], u3["l"], u3["c"]
    debut = int(np.searchsorted(u3["fin"], us["fin"][max(i_ext, mb)], side="left"))
    if debut >= b:
        return _non("attente")
    if sens == -1:
        touche = next((x for x in range(debut, b) if h3[x] >= z_bas), None)
    else:
        touche = next((x for x in range(debut, b) if l3[x] <= z_haut), None)
    if touche is None:
        return _non("zone pas touchée")
    if b - touche > p["attente_mss_bougies3"]:
        return _non("MSS trop tardif")
    seg = slice(touche, b)
    xi = touche + int(np.argmax(h3[seg]) if sens == -1 else np.argmin(l3[seg]))
    extreme = h3[xi] if sens == -1 else l3[xi]
    idx_sw = np.where(u3["st"]["sl" if sens == -1 else "sh"])[0]
    sw = [x for x in idx_sw if x < xi and x + u3["k"] <= b]
    if not sw:
        return _non("pas de swing 3 min")
    niveau = (l3 if sens == -1 else h3)[sw[-1]]
    mss = c3[b] < niveau <= c3[b - 1] if sens == -1 else c3[b] > niveau >= c3[b - 1]
    if not mss:
        return _non("pas de MSS 3 min")
    if sens == -1:
        fond = l3[xi:b + 1].min(); entree = fond + p["entree_ote"] * (extreme - fond)
    else:
        fond = h3[xi:b + 1].max(); entree = fond - p["entree_ote"] * (fond - extreme)
    stop = extreme + sens * p["marge_stop_atr"] * u3["atr"][b]
    risque = (entree - stop) * sens
    if risque <= 0:
        return _non("risque nul")
    cible = entree + sens * p["r_cible"] * risque
    DIAG[COURANT]["signal"] += 1
    sig = _signal(sens, entree, stop, cible, "ob_ote_mss3", p["r_cible"] - 1e-9, "limite", p["expiration_minutes"])
    if sig:
        sig.update({"be_r": p.get("break_even_r"), "etoiles": score, "fvg": fvg, "htf": htf,
                    "zone": [round(float(z_bas), 4), round(float(z_haut), 4)], "ut": p["ut"]})
    return sig


# ---------------------------------------------------------------- 3. Support / résistance
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


# ---------------------------------------------------------------- 4. Volume Profile
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


FONCTIONS = {"ict": ict, "perso": perso, "sr": sr, "vp": vp}
NOMS = {"ict": "ICT strict", "perso_1h": "Perso 1 h", "perso_2h": "Perso 2 h", "sr": "Support / résistance",
        "vp": "Volume Profile"}


def fonction(nom, p):
    """Une stratégie de la config peut réutiliser une fonction (ex. perso_1h et perso_2h → perso)."""
    return FONCTIONS[p.get("fonction", nom)]


STRATEGIES = FONCTIONS
