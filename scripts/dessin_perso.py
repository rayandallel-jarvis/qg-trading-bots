"""Dessine un trade du bot Perso : vue 1 h (structure, OB, OTE) et zoom 3 min (MSS, entrée, stop, TP).
Usage : python3 scripts/dessin_perso.py trades.json bougies.pkl index sortie.png"""
import json, sys
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

PARIS = "Europe/Paris"
HAUSSE, BAISSE = "#1f9d6b", "#d64545"
FOND, GRILLE, ENCRE, ENCRE2 = "#ffffff", "#ececec", "#1d1d1f", "#6b6b70"
OB_C, OTE_C, BOS_C, MSS_C = "#e8a33d", "#4a7fd6", "#7b5cc4", "#7b5cc4"


def agreger(df, regle):
    g = df.resample(regle, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    g.index = g.index.tz_convert(PARIS)
    return g


def bougies(ax, d, larg):
    x = range(len(d))
    for k, (o, h, l, c) in enumerate(d[["open", "high", "low", "close"]].to_numpy()):
        col = HAUSSE if c >= o else BAISSE
        ax.vlines(k, l, h, color=col, lw=0.8, zorder=2)
        ax.add_patch(Rectangle((k - larg / 2, min(o, c)), larg, max(abs(c - o), 1e-9),
                               facecolor=col, edgecolor=col, lw=0.5, zorder=3))
    ax.set_xlim(-1, len(d))
    return list(x)


def pos(d, t):
    t = pd.Timestamp(t)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    return int(d.index.searchsorted(t.tz_convert(PARIS), side="right")) - 1


def etiquettes_x(ax, d, n=6, fmt="%d/%m %Hh"):
    pas = max(1, len(d) // n)
    ix = list(range(0, len(d), pas))
    ax.set_xticks(ix)
    ax.set_xticklabels([d.index[i].strftime(fmt) for i in ix], fontsize=8, color=ENCRE2)


def style(ax):
    ax.set_facecolor(FOND)
    ax.grid(axis="y", color=GRILLE, lw=0.6)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#cfcfd4")
    ax.tick_params(colors=ENCRE2, labelsize=8)


def texte(ax, x, y, msg, col, ha="left", va="center"):
    ax.text(x, y, msg, color=col, fontsize=8.5, ha=ha, va=va, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=col, lw=0.8), zorder=6)


def main(f_trades, f_df, idx, sortie):
    t = json.load(open(f_trades))[int(idx)]
    df = pd.read_pickle(f_df)
    dz = t["dessin"]
    vend = t["sens"] == -1
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 6.6), gridspec_kw={"width_ratios": [1.05, 1]})
    fig.patch.set_facecolor(FOND)

    # ---------------- vue 1 h
    h1 = agreger(df, "1h")
    i_ob, i_bos, i_ent = pos(h1, dz["ob_t"]) + 1, pos(h1, dz["bos_t"]) + 1, pos(h1, t["debut"])
    i_ob = pos(h1, pd.Timestamp(dz["ob_t"]) + pd.Timedelta(minutes=1))
    i_bos = pos(h1, pd.Timestamp(dz["bos_t"]) + pd.Timedelta(minutes=1))
    i_fin = pos(h1, t["fin"])
    g, d_ = max(0, i_ob - 30), min(len(h1), i_fin + 8)
    v = h1.iloc[g:d_]
    bougies(a1, v, 0.6)
    style(a1); etiquettes_x(a1, v)
    o, b, e, fi = i_ob - g, i_bos - g, i_ent - g, i_fin - g
    # OB
    a1.add_patch(Rectangle((o - 0.5, dz["ob_l"]), len(v) - o, dz["ob_h"] - dz["ob_l"],
                           facecolor=OB_C, alpha=0.18, edgecolor=OB_C, lw=1.2, zorder=1))
    texte(a1, o - 0.6, dz["ob_h"] if vend else dz["ob_l"], "OB 1 h", OB_C, ha="right")
    # OTE
    a1.axhspan(dz["ote"][0], dz["ote"][1], xmin=(o + 1) / len(v), color=OTE_C, alpha=0.08, zorder=0)
    for y in dz["ote"]:
        a1.hlines(y, o, len(v) - 1, color=OTE_C, lw=0.8, ls=(0, (4, 3)), zorder=1)
    texte(a1, len(v) - 1, (dz["ote"][0] + dz["ote"][1]) / 2, "OTE 62–79 %", OTE_C, ha="right")
    # BOS
    a1.hlines(dz["bos_niv"], b - 12, b + 0.5, color=BOS_C, lw=1.4, zorder=4)
    texte(a1, b - 12, dz["bos_niv"], "Cassure du dernier\n" + ("plus bas (BOS)" if vend else "plus haut (BOS)"),
          BOS_C, ha="right")
    # entrée
    a1.annotate("Entrée", xy=(e, t["entree"]), xytext=(e - 6, t["entree"] + (1 if vend else -1) * 0.25 *
                (dz["jambe_haut"] - dz["jambe_bas"])), color=ENCRE, fontsize=9, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=ENCRE, lw=1.2), zorder=7)
    a1.axvspan(e - 0.5, fi + 0.5, color="#000000", alpha=0.04, zorder=0)
    a1.set_title("1. Structure 1 h : cassure, order block et zone OTE", fontsize=11, color=ENCRE, loc="left",
                 fontweight="bold")

    # ---------------- zoom 3 min
    m3 = agreger(df, "3min")
    j_t = pos(m3, dz["touche_t"]); j_x = pos(m3, dz["ext_t"]); j_m = pos(m3, dz["mss_t"])
    j_e = pos(m3, t["debut"]); j_f = pos(m3, t["fin"])
    g2 = max(0, j_t - 25); d2 = min(len(m3), j_f + 15)
    if d2 - g2 > 220:
        d2 = g2 + 220
    w = m3.iloc[g2:d2]
    bougies(a2, w, 0.62)
    style(a2); etiquettes_x(a2, w, fmt="%d/%m %H:%M")
    n = len(w)
    a2.add_patch(Rectangle((-0.5, dz["ob_l"]), n, dz["ob_h"] - dz["ob_l"], facecolor=OB_C, alpha=0.12,
                           edgecolor=OB_C, lw=0.8, zorder=0))
    texte(a2, 0, dz["ob_l"] if vend else dz["ob_h"], "OB 1 h", OB_C)
    xm = j_m - g2
    a2.hlines(dz["mss_niv"], max(0, xm - 18), xm + 1, color=MSS_C, lw=1.4, zorder=4)
    texte(a2, max(0, xm - 18), dz["mss_niv"], "MSS 3 min", MSS_C, ha="right")
    xe = max(0, j_e - g2)
    xf = min(n - 1, j_f - g2)
    for y, lab, col in [(t["stop_initial"], "Stop (au-delà de l'OB)", BAISSE), (t["entree"], "Entrée 61,8 %", ENCRE),
                        (t["cible"], "TP 2R", HAUSSE)]:
        a2.hlines(y, xm, n - 1, color=col, lw=1.3, ls="-" if col == ENCRE else (0, (5, 3)), zorder=5)
        texte(a2, n - 1, y, lab, col, ha="right")
    a2.add_patch(Rectangle((xe, min(t["entree"], t["cible"])), xf - xe, abs(t["cible"] - t["entree"]),
                           facecolor=HAUSSE, alpha=0.10, zorder=0))
    a2.add_patch(Rectangle((xe, min(t["entree"], t["stop_initial"])), xf - xe,
                           abs(t["stop_initial"] - t["entree"]), facecolor=BAISSE, alpha=0.10, zorder=0))
    a2.plot([xe], [t["entree"]], marker="o", ms=8, color=ENCRE, mec="white", mew=1.5, zorder=7)
    a2.plot([xf], [t["sortie"]], marker="X", ms=10, color=HAUSSE if t["r"] > 0 else BAISSE, mec="white",
            mew=1.5, zorder=7)
    a2.set_title("2. Zoom 3 min : contact, MSS, entrée, stop et objectif", fontsize=11, color=ENCRE, loc="left",
                 fontweight="bold")

    res = f'{t["r"]:+.2f}R  ({t["r"] * 50:+.0f} \\$ sur 10 000 \\$)'
    issue = "objectif 2R atteint" if t["motif"] == "objectif" else "stop touché"
    sens = "Vente" if vend else "Achat"
    debut = pd.Timestamp(t["debut"]).tz_convert(PARIS) if pd.Timestamp(t["debut"]).tzinfo else \
        pd.Timestamp(t["debut"]).tz_localize("UTC").tz_convert(PARIS)
    extra = (" + FVG" if t.get("fvg") else "") + ("".join(f" + visible {x}" for x in t.get("htf", [])))
    fig.suptitle(f'Bot Perso 1 h · Nasdaq 100 · {sens} du {debut:%d/%m/%Y à %H:%M} (heure de Paris) · '
                 f'{issue} · {res}', fontsize=12.5, color=ENCRE, fontweight="bold", x=0.01, ha="left", y=0.995)
    fig.text(0.01, 0.935, f'Score de l\'OB : {t["etoiles"]}/5 (base{extra})   ·   Frais IG inclus   ·   '
             f'Trade réel du backtest, bougies Dukascopy', fontsize=9.5, color=ENCRE2)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(sortie, dpi=150, facecolor=FOND)


if __name__ == "__main__":
    main(*sys.argv[1:5])
