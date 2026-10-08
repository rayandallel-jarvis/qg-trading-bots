"""Test sur historique (filtre avant déploiement) : mêmes règles que le bot en direct.

Hypothèses prudentes : si le stop et l'objectif sont touchés dans la même bougie, c'est le stop
qui compte ; un ordre au marché est exécuté à l'ouverture de la bougie suivante ; frais et
glissement déduits de chaque trade.
"""
import sys
from datetime import datetime, timezone

from .commun import Garde, charger_config, ecrire_json, notifier, resultat_r, statistiques, taille
from .marche import Marche
from .strategies import NOMS, STRATEGIES


def _minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def simuler(m, nom, symbole, a, cfg, capital=100_000):
    p = cfg["strategies"][nom]
    f = STRATEGIES[nom]
    garde = Garde(cfg)
    cloture = _minutes(cfg["risque"]["cloture_actions"])
    trades, pos, att = [], None, None
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
            if sortie is not None:
                r = resultat_r(sens, pos["entree"], sortie, pos["stop"], a)
                trades.append({**pos, "sortie": round(float(sortie), 4), "motif": motif, "r": r,
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
                       "entree": round(float(entree), 4), "stop": att["stop"], "cible": att["cible"],
                       "r_prevu": att["r_prevu"], "jour": att["jour"], "debut": str(m.fin1[i])}
                garde.ouvert(nom, symbole, att["jour"])
                att = None
                # un stop touché dans la bougie d'entrée d'un ordre limite compte comme perte
                if (sens == 1 and m.l[i] <= pos["stop"]) or (sens == -1 and m.h[i] >= pos["stop"]):
                    r = resultat_r(sens, pos["entree"], pos["stop"], pos["stop"], a)
                    trades.append({**pos, "sortie": pos["stop"], "motif": "stop", "r": r, "fin": str(m.fin1[i])})
                    garde.ferme(nom, pos["jour"], r)
                    pos = None
            continue

        if a["type"] == "action" and (m.paris_min[i] >= cloture - 10 or derniere_de_session):
            continue
        if not garde.autorise(nom, symbole, jour):
            continue
        s = f(m, i, p)
        if not s:
            continue
        if s["sens"] == -1 and not a.get("vente_a_decouvert", True):
            continue
        qte, _, refus = taille(s, capital, a, cfg)
        if refus:
            continue
        att = {**s, "expire": i + s["expiration"], "jour": jour}
    return trades


def main():
    cfg = charger_config()
    from .donnees import Donnees
    d = Donnees()
    jours = cfg["backtest"]["jours"]
    resultats, tous = {}, []
    for symbole, a in cfg["actifs"].items():
        df = d.historique(symbole, a["type"], jours + 5)
        print(symbole, len(df), "bougies")
        if len(df) < 500:
            continue
        m = Marche(df, a["type"], cfg["swing_k"], cfg["strategies"]["vp"]["zone_valeur"])
        for nom, p in cfg["strategies"].items():
            if not p.get("actif", True):
                continue
            tr = simuler(m, nom, symbole, a, cfg)
            resultats[f"{nom}|{symbole}"] = statistiques([t["r"] for t in tr])
            tous += tr
            print(nom, symbole, resultats[f"{nom}|{symbole}"])

    par_strat, lignes = {}, []
    for nom in cfg["strategies"]:
        rs = [t["r"] for t in sorted(tous, key=lambda t: t["fin"]) if t["strategie"] == nom]
        st = statistiques(rs)
        st["feu_vert"] = bool(st["trades"] >= cfg["backtest"]["trades_min"] and st.get("esperance_r", 0) > 0)
        par_strat[nom] = st
        if st["trades"]:
            lignes.append(f"{NOMS[nom]} : {st['trades']} trades, {st['esperance_r']:+.2f}R/trade"
                          f"{' ✅' if st['feu_vert'] else ''}")
        else:
            lignes.append(f"{NOMS[nom]} : 0 trade")
    ecrire_json("backtest.json", {
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jours": jours, "par_strategie": par_strat, "par_strategie_actif": resultats,
        "trades": sorted(tous, key=lambda t: t["fin"])[-400:],
    })
    notifier("Backtest 4 semaines\n" + "\n".join(lignes), titre="QG Trading - backtest", tags="bar_chart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
