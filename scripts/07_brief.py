"""
Step 7 - One-page analyst brief (A4 PDF) to attach to applications.

Builds docs/shore_camera_brief.pdf from the figures of step 5 and the numbers
in the step 4 / step 6 outputs, so the brief always matches the analysis.

Usage:
    python scripts/07_brief.py
"""

import os
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import pandas as pd

PROC = "data/processed"
IMG = "docs/images"
OUT_PDF = "docs/shore_camera_brief.pdf"
OUT_PNG = "docs/images/brief_preview.png"
REPO = "github.com/netanel35001-lab/shore-camera-dri"
LINKEDIN = "linkedin.com/in/netanelshaprut"

BLUE, BLUE_DARK, ORANGE = "#2a78d6", "#104281", "#eb6834"
INK, INK_2, INK_3, RULE, TILE = "#0b0b0b", "#52514e", "#8a8984", "#d9d7d0", "#f3f2ee"
W, H = 8.27, 11.69                      # A4 portrait, inches
M = 0.55                                # page margin, inches


# --- Numbers from the analysis outputs ------------------------------------------------
def load_numbers():
    by = pd.read_csv(os.path.join(PROC, "dri_by_class.csv"))
    tr = pd.read_csv(os.path.join(PROC, "dri_transits.csv"))
    rk = pd.read_csv(os.path.join(PROC, "second_camera_ranking.csv"))

    def get(cls, sensor, col):
        row = by[(by["vessel_class"] == cls) & (by["sensor"] == sensor)]
        return float(row[col].iloc[0])

    best = rk.sort_values("recognized_all_pct", ascending=False).iloc[0]
    return dict(
        rec=get("ALL", "BEST", "recognize_or_better_central"),
        rec_lo=get("ALL", "BEST", "recognize_or_better_low"),
        rec_hi=get("ALL", "BEST", "recognize_or_better_high"),
        pleasure=get("pleasure", "BEST", "recognize_or_better_central"),
        pleasure_none=get("pleasure", "BEST", "none_pct"),
        ferry=get("passenger", "BEST", "recognize_or_better_central"),
        eo_wide=get("ALL", "EO_wide", "recognize_or_better_central"),
        eo_zoom=get("ALL", "EO_zoom", "recognize_or_better_central"),
        transits=(tr["BEST_central"] >= 2).mean() * 100,
        n_sites=len(rk),
        cam2_all=float(best["recognized_all_pct"]),
        cam2_gain=float(best["gain_all_pts"]),
        cam2_small_gain=float(best["gain_small_pts"]),
    )


# --- Layout helpers (positions in inches from the bottom-left corner) -----------------
def txt(fig, x, y, s, size=9, color=INK, weight="normal", width=None, va="top",
        ha="left", style="normal", linespacing=1.35):
    if width:
        s = "\n".join(textwrap.fill(p, width) for p in s.split("\n"))
    fig.text(x / W, y / H, s, fontsize=size, color=color, weight=weight, va=va, ha=ha,
             style=style, linespacing=linespacing, family="DejaVu Sans")


def image(fig, path, x, y_top, width):
    img = mpimg.imread(path)
    h = width * img.shape[0] / img.shape[1]
    ax = fig.add_axes([x / W, (y_top - h) / H, width / W, h / H])
    ax.imshow(img, interpolation="none")   # embed the original pixels
    ax.axis("off")
    return y_top - h


def rule(fig, y, x0=M, x1=W - M):
    fig.add_artist(plt.Line2D([x0 / W, x1 / W], [y / H, y / H], color=RULE, lw=0.8))


def heading(fig, x, y, s):
    txt(fig, x, y, s.upper(), size=8, color=BLUE_DARK, weight="bold")


def bullets(fig, x, y, items, width, size=8.4, gap=0.1):
    """Bulleted list; returns the y below the last item."""
    for it in items:
        lines = textwrap.wrap(it, width)
        txt(fig, x, y, "•", size=size, color=BLUE)
        txt(fig, x + 0.14, y, "\n".join(lines), size=size, color=INK_2)
        y -= len(lines) * size * 1.35 / 72 + gap
    return y


# --- Page -----------------------------------------------------------------------------
def main():
    n = load_numbers()
    fig = plt.figure(figsize=(W, H))
    fig.patch.set_facecolor("white")

    # Header
    txt(fig, M, H - 0.5, "PORTFOLIO PROJECT  ·  MARITIME VISUAL ANALYSIS", size=7.5,
        color=INK_3, weight="bold")
    txt(fig, W - M, H - 0.5, "Netanel Shaprut  ·  Geospatial & Visual Data Analyst",
        size=7.5, color=INK_3, ha="right")
    txt(fig, M, H - 0.72, "What can a shore camera actually see?", size=21, weight="bold")
    txt(fig, M, H - 1.17,
        "Detect / recognize / identify coverage of a 30 m EO/IR camera at the port of "
        "Esbjerg, Denmark, tested against one week of real AIS traffic "
        "(1.7 M positions, 159 vessels, 13-19 July 2026).", size=9.5, color=INK_2, width=100)
    rule(fig, H - 1.62)

    # KPI tiles
    tiles = [
        (f"{n['rec']:.0f}%", f"of moving-vessel time the camera recognizes the vessel "
                             f"({n['rec_lo']:.0f}-{n['rec_hi']:.0f}% across height assumptions)"),
        (f"{n['transits']:.0f}%", "of vessel transits are recognized at least once"),
        (f"{n['pleasure']:.0f}% / {n['ferry']:.0f}%", f"pleasure craft vs ferries recognized. "
                             f"Pleasure craft not seen at all {n['pleasure_none']:.0f}% of the time"),
        (f"+{n['cam2_gain']:.0f} pts", f"from a second camera at the best of {n['n_sites']} "
                             f"tested coastal sites ({n['rec']:.0f}% to {n['cam2_all']:.0f}%)"),
    ]
    gap, top, th = 0.12, H - 1.8, 1.08
    tw = (W - 2 * M - 3 * gap) / 4
    for i, (big, small) in enumerate(tiles):
        x = M + i * (tw + gap)
        fig.add_artist(plt.Rectangle((x / W, (top - th) / H), tw / W, th / H,
                                     color=TILE, lw=0, transform=fig.transFigure))
        color = ORANGE if i == 2 else BLUE_DARK
        txt(fig, x + 0.12, top - 0.1, big, size=19 if len(big) < 8 else 16,
            color=color, weight="bold")
        txt(fig, x + 0.12, top - 0.5, small, size=7.2, color=INK_2, width=30,
            linespacing=1.25)

    # Hero figure
    y = image(fig, os.path.join(IMG, "fig1_dri_zones.png"), M, top - th - 0.15, W - 2 * M)

    # Bar chart (left) + what it means (right)
    y_row = y - 0.12
    col_w = 3.95
    y_left = image(fig, os.path.join(IMG, "fig3_by_class.png"), M, y_row, col_w)
    xr = M + col_w + 0.3
    heading(fig, xr, y_row - 0.05, "What it means for an operator")
    y_r = bullets(fig, xr, y_row - 0.32, [
        "The approach channel is covered: ferries and cargo ships are recognized most of "
        "the time, and almost every transit is recognized at least once.",
        f"Small craft are the gap. Pleasure craft are not seen at all "
        f"{n['pleasure_none']:.0f}% of the time, and many small boats carry no AIS, "
        "so the real gap is larger.",
        f"Zoom lifts recognition from {n['eo_wide']:.0f}% to {n['eo_zoom']:.0f}%, but "
        "through a 3° field of view. Range versus coverage is the core design choice.",
        f"A second camera at the Skallingen tip adds +{n['cam2_gain']:.0f} pts overall "
        f"(+{n['cam2_small_gain']:.1f} for small craft). All {n['n_sites']} candidate "
        "sites are ranked and mapped.",
    ], width=50)

    # Method / validation
    y_txt = min(y_left, y_r) - 0.15
    rule(fig, y_txt + 0.05)
    half = (W - 2 * M - 0.3) / 2
    heading(fig, M, y_txt - 0.08, "Method")
    txt(fig, M, y_txt - 0.3,
        "AIS cleaned (duplicates, position spikes, moored-boat speed jitter, land "
        "transponders) and split into transits. Line of sight along 1,800 rays over the "
        "Copernicus GLO-30 elevation model, with Earth curvature and refraction. Visible "
        "height becomes pixels on target for three sensors (EO wide 25 mm, EO zoom 100 mm, "
        "thermal 50 mm), EO only in daylight. Johnson criteria: 2 / 8 / 12.8 px. Every "
        "number is computed with vessel heights -30% and +30%.",
        size=7.8, color=INK_2, width=62)
    xv = M + half + 0.3
    heading(fig, xv, y_txt - 0.08, "Validation & data quality")
    txt(fig, xv, y_txt - 0.3,
        "Matches the closed-form curvature result exactly over open sea, and agrees with "
        "Google Earth views from the camera position. Findings that changed the rules: 22% "
        "duplicate receptions, 14,504 'moving' positions from moored boats, a transponder "
        "on a trailer driving through town, offshore-wind vessels hidden as 'Other'. "
        "Limits: geometric best case - no haze, glare or sea state.",
        size=7.8, color=INK_2, width=62)

    # Footer
    rule(fig, 0.62)
    txt(fig, M, 0.5, f"Code, figures and full write-up:  {REPO}", size=8, color=INK,
        weight="bold")
    txt(fig, W - M, 0.5, LINKEDIN, size=8, color=INK_2, ha="right")
    txt(fig, M, 0.3, "Data: Danish Maritime Authority AIS  ·  Copernicus DEM GLO-30  ·  "
        "Python, DuckDB, NumPy, rasterio, matplotlib", size=6.8, color=INK_3)

    os.makedirs("docs/images", exist_ok=True)
    fig.savefig(OUT_PDF, dpi=300, facecolor="white")
    fig.savefig(OUT_PNG, dpi=110, facecolor="white")
    print(f"Wrote {OUT_PDF} and {OUT_PNG}")


if __name__ == "__main__":
    main()