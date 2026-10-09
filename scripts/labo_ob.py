"""Labo des stratégies order block (modèle Perso) : unités 15 min / 30 min / 1 h et les 7 pistes.

Simule chaque variante sur avril 2025 → 8 oct. 2026 et garde tous les trades, pour :
  1) le tri piste par piste ;
  2) la méthode glissante (chaque mois : meilleur réglage sur les 6 mois précédents, tradé le mois suivant).
Usage : python3 scripts/labo_ob.py variantes.json sortie.json [ACTIF ...]
variantes.json = {"nom": {param: valeur, ...}}  (appliqués à perso_1h ; "ut" obligatoire)
"""
import copy, json, os, sys
from datetime import date
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from bots.commun import charger_config

FIN, DEBUT, COMPTE_DES = date(2026, 10, 8), date(2025, 3, 1), "2025-04-01"
CHAMPS = ("actif", "sens", "debut", "fin", "r", "r_brut", "motif", "etoiles", "pm", "entree", "stop_initial",
          "cible", "sortie")


def _travail(args):
    sym, variantes = args
    import bots.strategies as S
    from bots.backtest import construire_marche, simuler
    from bots.donnees_duka import historique
    cfg0 = charger_config()
    a = cfg0["actifs"][sym]
    df = historique(a["instrument"], (FIN - DEBUT).days + 1, fin=FIN, travailleurs=8)
    m = construire_marche(df, a, cfg0)
    out = {}
    for nom, reglages in variantes.items():
        cfg = copy.deepcopy(cfg0)
        cfg["strategies"]["perso_1h"].update({"age_max_h": 72, **reglages})
        S.OB_UTILISES.clear()
        tr = simuler(m, "perso_1h", sym, a, cfg)
        out[nom] = [{k: t.get(k) for k in CHAMPS} for t in tr if t["debut"] >= COMPTE_DES]
    return sym, out


if __name__ == "__main__":
    variantes = json.load(open(sys.argv[1]))
    actifs = sys.argv[3:] or ["NAS100", "US500", "OR"]
    with ProcessPoolExecutor(2) as ex:
        res = dict(ex.map(_travail, [(s, variantes) for s in actifs]))
    json.dump(res, open(sys.argv[2], "w"), default=str)
    for nom in variantes:
        ligne = []
        for s in actifs:
            tr = res[s][nom]
            ligne.append(f'{s} {len(tr):4d} tr {round(sum(t["r"] for t in tr) * 50):6d} $')
        print(f'{nom:28s} ' + " | ".join(ligne), flush=True)
    print("FIN")
