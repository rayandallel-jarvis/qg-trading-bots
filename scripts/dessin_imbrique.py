"""Dessine un trade du modèle « OB dans un OB » : vue 4 h (gros OB) et vue 1 h (petit OB, mèche, confirmation).
Heures affichées = heure de Paris. Usage : python3 scripts/dessin_imbrique.py resultats.json variante index sortie.png
"""
import json, sys, os
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from bots.commun import charger_config
from bots.donnees_duka import historique

PARIS = "Europe/Paris"
HAUSSE, BAISSE, FOND, ENCRE, ENCRE2, GRILLE = "#1f9d6b", "#d64545", "#ffffff", "#1d1d1f", "#6b6b70", "#ececec"
OB4_C, OB1_C, CONF_C = "#7b5cc4", "#e8a33d", "#4a7fd6"


def agreger(df, regle, decal=None):
    g = df.resample(regle, label="left", closed="left", offset=decal).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    g.index = g.index.tz_convert(PARIS)
    return g


def bougies(ax, d, larg=0.6):
    for k, (o, h, l, c) in enumerate(d[["open", "high", "low", "close"]].to_numpy()):
        col = HAUSSE if c >= o else BAISSE
        ax.vlines(k, l, h, color=col, lw=0.8, zorder=2)
        ax.add_patch(Rectangle((k - larg / 2, min(o, c)), larg, max(abs(c - o), 1e-9), facecolor=col,
                               edgecolor=col, lw=0.5, zorder=3))
    ax.set_xlim(-1, len(d))


def pos(d, t):
    t = pd.Timestamp(t)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    return int(d.index.searchsorted(t.tz_convert(PARIS), side="right")) - 1


def style(ax, d, n=6, fmt="%d/%m %Hh"):
    ax.set_facecolor(FOND); ax.grid(axis="y", color=GRILLE, lw=0.6)
    for s_ in ("top", "right"):
        ax.spines[s_].set_visible(False)
    pas = max(1, len(d) // n)
    ix = list(range(0, len(d), pas))
    ax.set_xticks(ix); ax.set_xticklabels([d.index[i].strftime(fmt) for i in ix], fontsize=8, color=ENCRE2)
    ax.tick_params(colors=ENCRE2, labelsize=8)


def etiquette(ax, x, y, msg, col, ha="left"):
    ax.text(x, y, msg, color=col, fontsize=8.5, ha=ha, va="center", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=col, lw=0.8), zorder=6)


def main(f_res, variante, idx, sortie):
    t = json.load(open(f_res))[variante]["trades"][int(idx)]
    a = charger_config()["actifs"][t["actif"]]
    fin = pd.Timestamp(t["fin"]).date()
    df = historique(a["instrument"], 30, fin=min(fin + pd.Timedelta(days=3), date(2026, 10, 8)), travailleurs=6)
    dz = t["dessin"]
    s = t["sens"]
    fig, (a4, a1) = plt.subplots(1, 2, figsize=(15, 6.6), gridspec_kw={"width_ratios": [1, 1.1]})
    fig.patch.set_facecolor(FOND)

    h4 = agreger(df, "4h", "1h")
    j4 = pos(h4, pd.Timestamp(dz["ob4_t"]) + pd.Timedelta(minutes=1)); jf = pos(h4, t["fin"])
    g = max(0, j4 - 25); d_ = min(len(h4), jf + 4)
    v = h4.iloc[g:d_]
    bougies(a4, v); style(a4, v)
    a4.add_patch(Rectangle((j4 - g - 0.5, dz["ob4"][0]), len(v) - (j4 - g), dz["ob4"][1] - dz["ob4"][0],
                           facecolor=OB4_C, alpha=0.15, edgecolor=OB4_C, lw=1.2, zorder=1))
    etiquette(a4, j4 - g - 0.6, dz["ob4"][1] if s == 1 else dz["ob4"][0], "Gros OB 4 h", OB4_C, ha="right")
    jb = pos(h4, pd.Timestamp(dz["bos4_t"]) + pd.Timedelta(minutes=1)) - g
    a4.annotate("BOS 4 h", xy=(jb, v["high"].iloc[jb] if s == 1 else v["low"].iloc[jb]),
                xytext=(jb, v["high"].max() if s == 1 else v["low"].min()), color=ENCRE, fontsize=8.5,
                ha="center", arrowprops=dict(arrowstyle="->", color=ENCRE2))
    je = pos(h4, t["debut"]) - g
    a4.axvspan(je - 0.5, jf - g + 0.5, color="#000", alpha=0.05, zorder=0)
    a4.set_title("1. Vue 4 h : tendance, gros OB, retour du prix", fontsize=11, color=ENCRE, loc="left",
                 fontweight="bold")

    h1 = agreger(df, "1h")
    k1 = pos(h1, pd.Timestamp(dz["ob1_t"]) + pd.Timedelta(minutes=1)); ke = pos(h1, t["debut"]); kf = pos(h1, t["fin"])
    g1 = max(0, k1 - 20); d1 = min(len(h1), max(kf, ke) + 6)
    if d1 - g1 > 110:
        d1 = g1 + 110
    w = h1.iloc[g1:d1]
    bougies(a1, w, 0.62); style(a1, w)
    n = len(w)
    a1.axhspan(dz["ob4"][0], dz["ob4"][1], color=OB4_C, alpha=0.07, zorder=0)
    a1.add_patch(Rectangle((k1 - g1 - 0.5, dz["ob1"][0]), n - (k1 - g1), dz["ob1"][1] - dz["ob1"][0],
                           facecolor=OB1_C, alpha=0.20, edgecolor=OB1_C, lw=1.2, zorder=1))
    etiquette(a1, k1 - g1 - 0.6, dz["ob1"][1] if s == 1 else dz["ob1"][0], "OB 1 h", OB1_C, ha="right")
    km = pos(h1, pd.Timestamp(dz["meche_t"]) + pd.Timedelta(minutes=1)) - g1
    kc = pos(h1, pd.Timestamp(dz["conf_t"]) + pd.Timedelta(minutes=1)) - g1
    for k_, lab in ((km, "mèche"), (kc, "confirmation")):
        y = w["low"].iloc[k_] if s == 1 else w["high"].iloc[k_]
        a1.annotate(lab, xy=(k_, y), xytext=(k_, y - s * 0.25 * (w["high"].max() - w["low"].min())),
                    color=CONF_C, fontsize=8.5, ha="center", fontweight="bold",
                    arrowprops=dict(arrowstyle="->", color=CONF_C))
    xe = ke - g1
    for y, lab, col in ((t["stop_initial"], "Stop", BAISSE), (t["entree"], "Entrée", ENCRE),
                        (t["cible"], f'TP liquidité ({t["r_prevu"]}R)', HAUSSE)):
        a1.hlines(y, xe, n - 1, color=col, lw=1.3, ls="-" if col == ENCRE else (0, (5, 3)), zorder=5)
        etiquette(a1, n - 1, y, lab, col, ha="right")
    a1.plot([xe], [t["entree"]], marker="o", ms=8, color=ENCRE, mec="white", mew=1.5, zorder=7)
    xf = min(n - 1, kf - g1)
    a1.plot([xf], [t["sortie"]], marker="X", ms=10, color=HAUSSE if t["r"] > 0 else BAISSE, mec="white",
            mew=1.5, zorder=7)
    a1.set_title("2. Vue 1 h : BOS, petit OB, mèche puis confirmation", fontsize=11, color=ENCRE, loc="left",
                 fontweight="bold")
    deb = pd.Timestamp(t["debut"]).tz_convert(PARIS)
    issue = "TP atteint" if t["motif"] == "objectif" else ("stop touché" if t["motif"] == "stop" else "clôturé")
    nom = {"NAS100": "Nasdaq 100", "US500": "S&P 500", "OR": "Or"}.get(t["actif"], t["actif"])
    fig.suptitle(f'OB dans un OB · {nom} · {"Achat" if s == 1 else "Vente"} du {deb:%d/%m/%Y à %H:%M} (Paris) · '
                 f'{issue} · {t["r"]:+.2f}R ({t["r"] * 50:+.0f} \\$ sur 10 000 \\$)', fontsize=12.5, color=ENCRE,
                 fontweight="bold", x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(sortie, dpi=140, facecolor=FOND)


if __name__ == "__main__":
    main(*sys.argv[1:5])
