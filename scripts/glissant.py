"""Méthode glissante sur les trades simulés par labo_ob.py.

Chaque mois M (oct. 2025 → oct. 2026), pour chaque actif : on choisit la variante qui a gagné le plus
sur les 6 mois précédents (au moins TRADES_MIN trades, et un gain > 0 ; sinon l'actif ne trade pas ce mois-là),
puis on prend ses trades du mois M tels quels. Le choix ne voit jamais le mois tradé.
Usage : python3 scripts/glissant.py sortie.json resultats1.json [resultats2.json ...]
"""
import collections, json, sys

V1R, TRADES_MIN, FENETRE = 50.0, 10, 6
MOIS = [f"2025-{m:02d}" for m in range(10, 13)] + [f"2026-{m:02d}" for m in range(1, 11)]


def mois_prec(m, n):
    a, mm = int(m[:4]), int(m[5:])
    out = []
    for _ in range(n):
        mm -= 1
        if mm == 0:
            a, mm = a - 1, 12
        out.append(f"{a}-{mm:02d}")
    return out


def main(sortie, fichiers):
    res = collections.defaultdict(dict)
    for f in fichiers:
        for sym, variantes in json.load(open(f)).items():
            res[sym].update(variantes)
    journal, par_mois = [], collections.defaultdict(float)
    trades_oos = []
    for m in MOIS:
        fen = set(mois_prec(m, FENETRE))
        for sym, variantes in res.items():
            meilleur, score = None, 0.0
            for nom, tr in variantes.items():
                t_fen = [t for t in tr if t["fin"][:7] in fen]
                g = sum(t["r"] for t in t_fen)
                if len(t_fen) >= TRADES_MIN and g > score:
                    meilleur, score = nom, g
            t_m = [t for t in res[sym][meilleur] if t["fin"][:7] == m] if meilleur else []
            g_m = sum(t["r"] for t in t_m) * V1R
            par_mois[m] += g_m
            trades_oos += t_m
            journal.append({"mois": m, "actif": sym, "choix": meilleur, "gain_fenetre": round(score * V1R),
                            "trades": len(t_m), "gain": round(g_m)})
    cum = pic = dd = 0.0
    for t in sorted(trades_oos, key=lambda t: t["fin"]):
        cum += t["r"] * V1R; pic = max(pic, cum); dd = min(dd, cum - pic)
    bilan = {"gain": round(cum), "trades": len(trades_oos), "pire_baisse": round(dd),
             "mois": {k: round(v) for k, v in par_mois.items()},
             "mois_pos": f'{sum(v > 0 for v in par_mois.values())}/{len(par_mois)}',
             "reussite": round(sum(t["r"] > 0 for t in trades_oos) / max(1, len(trades_oos)), 2)}
    json.dump({"bilan": bilan, "journal": journal}, open(sortie, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(bilan, ensure_ascii=False))
    for j in journal:
        print(f'{j["mois"]} {j["actif"]:7s} {str(j["choix"]):34s} fenêtre {j["gain_fenetre"]:6d} $ → {j["trades"]:3d} tr {j["gain"]:6d} $')


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
