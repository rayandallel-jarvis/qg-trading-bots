"""Test sur historique (filtre avant déploiement) : mêmes règles que le bot en direct.

Hypothèses prudentes : si le stop et l'objectif sont touchés dans la même bougie, c'est le stop
qui compte ; un ordre au marché est exécuté à l'ouverture de la bougie suivante ; frais et
glissement déduits de chaque trade.
"""
import sys
from datetime import datetime, timezone

import numpy as np

from .commun import Garde, charger_config, ecrire_json, notifier, resultat_r, statistiques, taille
from .marche import Marche


def construire_marche(df, a, cfg):
    st = cfg["strategies"]
    return Marche(df, a["type"], cfg["swing_k"], st["vp"]["zone_valeur"])
from collections import Counter

from . import strategies as S
from .strategies import DIAG, NOMS, fonction

REFUS = Counter()


def _minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def simuler(m, nom, symbole, a, cfg, capital=100_000):
    p = cfg["strategies"][nom]
    f = fonction(nom, p)
    garde = Garde(cfg)
    cloture = _minutes(cfg["risque"]["cloture_actions"])
    trades, pos, att = [], None, None
    # CFD : fermeture avant une longue coupure du marché (week-end) et pas d'entrée dans les 30 min qui précèdent
    trou = np.zeros(m.n, dtype=bool)
    proche_trou = np.zeros(m.n, dtype=bool)
    if a["type"] == "cfd" and a.get("ferme_week_end", True):
        ecarts = np.diff(m.t.asi8) / 6e10
        trou[:-1] = ecarts > 360
        trou[-1] = True
        idx = np.where(trou)[0]
        for j in idx:
            proche_trou[max(0, j - 30):j + 1] = True
    for i in range(m.n):
        jour = m.paris_jour[i]
        derniere_de_session = i == m.n - 1 or m.session[i + 1] != m.session[i]

        if pos:
            sens, sortie, motif = pos["sens"], None, None
            if sens == 1:
                if m.l[i] <= pos["stop"]:
                    sortie, motif = min(pos["stop"], m.o[i]), "stop"
                elif m.h[i] >= pos["cible"]:
                    sortie, motif = pos["cible"], "objectif"
            else:
                if m.h[i] >= pos["stop"]:
                    sortie, motif = max(pos["stop"], m.o[i]), "stop"
                elif m.l[i] <= pos["cible"]:
                    sortie, motif = pos["cible"], "objectif"
            if sortie is None and a["type"] == "action" and (m.paris_min[i] >= cloture or derniere_de_session):
                sortie, motif = m.c[i], "cloture"
            if sortie is None and trou[i]:
                sortie, motif = m.c[i], "cloture"
            if sortie is None and pos.get("be_r") and not pos.get("be"):
                seuil = pos["entree"] + sens * pos["be_r"] * abs(pos["entree"] - pos["stop_initial"])
                if (m.h[i] if sens == 1 else -m.l[i]) >= seuil * sens:
                    pos["stop"], pos["be"] = pos["entree"], True
            if sortie is not None:
                r = resultat_r(sens, pos["entree"], sortie, pos["stop_initial"], a, pos.get("pm"))
                brut = round(float((sortie - pos["entree"]) * sens / abs(pos["entree"] - pos["stop_initial"])), 3)
                trades.append({**pos, "sortie": round(float(sortie), 4), "motif": motif, "r": r, "r_brut": brut,
                               "fin": str(m.fin1[i])})
                garde.ferme(nom, pos["jour"], r)
                pos = None
            continue

        if att:
            sens = att["sens"]
            entree = None
            if att["ordre"] == "marche":
                entree = m.o[i]
                if (entree - att["stop"]) * sens <= 0 or (att["cible"] - entree) * sens <= 0:
                    entree = None
                    att = None
            elif (sens == 1 and m.l[i] <= att["entree"]) or (sens == -1 and m.h[i] >= att["entree"]):
                entree = att["entree"]
            elif i > att["expire"]:
                att = None
            if entree is not None:
                pos = {"strategie": nom, "actif": symbole, "setup": att["setup"], "sens": sens,
                       "entree": round(float(entree), 4), "stop": att["stop"], "stop_initial": att["stop"],
                       "cible": att["cible"], "be_r": att.get("be_r"),
                       "r_prevu": att["r_prevu"], "jour": str(att["jour"]), "debut": str(m.fin1[i]),
                       "pm": int(m.paris_min[i]),
                       **{k: att[k] for k in ("cible_type", "etoiles", "ut", "zone") if k in att}}
                garde.ouvert(nom, symbole, att["jour"])
                att = None
                # un stop touché dans la bougie d'entrée d'un ordre limite compte comme perte
                if (sens == 1 and m.l[i] <= pos["stop"]) or (sens == -1 and m.h[i] >= pos["stop"]):
                    r = resultat_r(sens, pos["entree"], pos["stop"], pos["stop_initial"], a, pos.get("pm"))
                    trades.append({**pos, "sortie": pos["stop"], "motif": "stop", "r": r, "r_brut": -1.0,
                                   "fin": str(m.fin1[i])})
                    garde.ferme(nom, pos["jour"], r)
                    pos = None
            continue

        if a["type"] == "action" and (m.paris_min[i] >= cloture - 10 or derniere_de_session):
            continue
        if proche_trou[i]:
            continue
        if not garde.autorise(nom, symbole, jour):
            continue
        S.COURANT = nom
        s = f(m, i, p)
        if not s:
            continue
        if s["sens"] == -1 and not a.get("vente_a_decouvert", True):
            REFUS[f"{nom}|{symbole}|vente interdite"] += 1
            continue
        qte, _, refus = taille(s, capital, a, cfg, int(m.paris_min[i]))
        if refus:
            REFUS[f"{nom}|{symbole}|{refus}"] += 1
            continue
        att = {**s, "expire": i + s["expiration"], "jour": jour}
    return trades


def en_valeur(rs, depart=10_000, risque_pct=0.5):
    """Résultat en dollars sur un compte de départ, en risquant risque_pct % du solde par trade."""
    solde, pic, dd = depart, depart, 0.0
    for r in rs:
        solde *= 1 + r * risque_pct / 100
        pic = max(pic, solde)
        dd = min(dd, solde - pic)
    return {"compte_depart": depart, "risque_pct": risque_pct, "valeur_1r": round(depart * risque_pct / 100, 2),
            "solde_final": round(solde, 2), "gain": round(solde - depart, 2),
            "gain_pct": round((solde / depart - 1) * 100, 2), "pire_baisse": round(dd, 2)}


def main():
    cfg = charger_config()
    jours = cfg["backtest"]["jours"]
    resultats, tous = {}, []
    d = None
    for symbole, a in cfg["actifs"].items():
        if not a.get("actif", True):
            continue
        if a.get("source") == "duka":
            from .donnees_duka import historique
            df = historique(a["instrument"], jours + 20)
        else:
            if d is None:
                from .donnees import Donnees
                d = Donnees()
            df = d.historique(symbole, a["type"], jours + 20)
        print(symbole, len(df), "bougies")
        if len(df) < 500:
            continue
        m = construire_marche(df, a, cfg)
        for nom, p in cfg["strategies"].items():
            if not p.get("actif", True):
                continue
            tr = simuler(m, nom, symbole, a, cfg)
            resultats[f"{nom}|{symbole}"] = statistiques([t["r"] for t in tr])
            resultats[f"{nom}|{symbole}"]["valeur"] = en_valeur([t["r"] for t in sorted(tr, key=lambda t: t["fin"])])
            tous += tr
            print(nom, symbole, resultats[f"{nom}|{symbole}"])

    par_strat, lignes = {}, []
    for nom in [n for n, q in cfg["strategies"].items() if q.get("actif", True)]:
        rs = [t["r"] for t in sorted(tous, key=lambda t: t["fin"]) if t["strategie"] == nom]
        st = statistiques(rs)
        brut = [t.get("r_brut", t["r"]) for t in tous if t["strategie"] == nom]
        st["esperance_brute_r"] = round(sum(brut) / len(brut), 3) if brut else None
        st["reussite_brute"] = round(sum(1 for x in brut if x > 0) / len(brut), 3) if brut else None
        st["valeur"] = en_valeur(rs)
        st["feu_vert"] = bool(st["trades"] >= cfg["backtest"]["trades_min"] and st.get("esperance_r", 0) > 0)
        par_strat[nom] = st
        if st["trades"]:
            v = st["valeur"]
            lignes.append(f"{NOMS.get(nom, nom)} : {st['trades']} trades, {v['gain']:+,.0f} $ sur 10 000 $ "
                          f"({v['gain_pct']:+.1f} %), {st['esperance_r']:+.2f}R/trade"
                          f"{' ✅' if st['feu_vert'] else ''}".replace(",", " "))
        else:
            lignes.append(f"{NOMS.get(nom, nom)} : 0 trade")
    ecrire_json("backtest.json", {
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jours": jours, "compte": "10 000 $ au départ, 0,5 % du solde risqué par trade (1R ≈ 50 $)",
        "par_strategie": par_strat, "entonnoir": {k: dict(v) for k, v in DIAG.items()},
        "refus": dict(REFUS), "par_strategie_actif": resultats,
        "trades": sorted(tous, key=lambda t: t["fin"])[-400:],
    })
    notifier(f"Backtest {jours} jours\n" + "\n".join(lignes), titre="QG Trading - backtest", tags="bar_chart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
