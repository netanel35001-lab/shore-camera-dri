"""
Step 5 - Presentation figures.

  fig1_dri_zones.png      HERO: what a 30 m shore camera can see by day - a small
                          craft (2.5 m) vs a cargo ship (18 m), with real traffic
  fig2_traffic_seen.png   one week of real AIS positions, coloured by the best DRI
                          level the camera reached for each of them
  fig3_by_class.png       share of time each vessel class is recognized or better,
                          with the -30 % / +30 % height range

Colours: an ordinal blue ramp for detect < recognize < identify, and orange for
"not seen" so blind spots stand out. Land is neutral grey.

Usage:
    python scripts/05_maps.py
"""

import importlib.util
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch
from pyproj import Transformer

# --- Style ------------------------------------------------------------------------
NOT_SEEN = "#eb6834"                                   # orange: blind spot
LEVEL_COLORS = ["#86b6ef", "#2a78d6", "#104281"]       # detect, recognize, identify
LAND, SEA_EDGE = "#dcdad3", "#8f8d86"
INK, INK_2, INK_3 = "#0b0b0b", "#52514e", "#8a8984"
LEVEL_NAMES = ["Not seen", "Detect", "Recognize", "Identify"]
CMAP = ListedColormap([NOT_SEEN] + LEVEL_COLORS)
NORM = BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5], CMAP.N)
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": INK_3,
    "axes.labelcolor": INK_2, "xtick.color": INK_3, "ytick.color": INK_3,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlecolor": INK,
})
# Label positions (lon, lat) checked against the DSM: land names on land, water on water.
PLACES = [("ESBJERG", 8.535, 55.505, "land"), ("FANØ", 8.41, 55.40, "land"),
          ("SKALLINGEN", 8.17, 55.548, "land"), ("Grådyb", 8.255, 55.462, "sea"),
          ("North Sea", 8.05, 55.47, "sea")]
CREDIT = ("AIS: Danish Maritime Authority, 13-19 Jul 2026  |  Elevation: Copernicus "
          "GLO-30 DEM  |  Model & analysis: N. Shaprut")


def load_step(name):
    path = os.path.join(os.path.dirname(__file__), name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def km_ring(ax, cam, km, to_geo, cx, cy):
    t = np.linspace(0, 2 * np.pi, 361)
    lon, lat = to_geo.transform(cx + np.sin(t) * km * 1000, cy + np.cos(t) * km * 1000)
    ax.plot(lon, lat, color=INK_2, lw=0.7, ls=(0, (4, 3)), zorder=4)
    lon_l, lat_l = to_geo.transform(cx - km * 1000 * np.sin(np.radians(30)),
                                    cy - km * 1000 * np.cos(np.radians(30)))
    ax.text(lon_l, lat_l, f"{km} km", fontsize=8, color=INK_2, ha="center", va="center",
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8), zorder=5)


def decorate(ax, cfg, to_geo, cx, cy, rings=(5, 10, 20)):
    cam, area = cfg["camera"], cfg["study_area"]
    for km in rings:
        km_ring(ax, cam, km, to_geo, cx, cy)
    ax.plot(cam["lon"], cam["lat"], marker="*", ms=15, color="white", mec=INK, mew=1.2,
            ls="none", zorder=6)
    ax.annotate("Camera\n30 m", (cam["lon"], cam["lat"]), xytext=(10, 8),
                textcoords="offset points", fontsize=8, color=INK, weight="bold", zorder=6)
    for name, lon, lat, kind in PLACES:
        ax.text(lon, lat, name, fontsize=9 if kind == "land" else 8.5,
                style="normal" if kind == "land" else "italic",
                color=INK_2 if kind == "land" else "#1c5cab", ha="center", va="center",
                weight="bold" if kind == "land" else "normal", zorder=5)
    # Scale bar (10 km) and north arrow, bottom-left.
    x0, y0 = area["lon_min"] + 0.03, area["lat_min"] + 0.02
    dlon = 10_000 / (111_320 * np.cos(np.radians(y0)))
    ax.plot([x0, x0 + dlon], [y0, y0], color=INK, lw=2.5, solid_capstyle="butt", zorder=6)
    ax.text(x0 + dlon / 2, y0 + 0.008, "10 km", ha="center", fontsize=8, color=INK, zorder=6)
    ax.annotate("N", xy=(x0 + 0.005, y0 + 0.085), xytext=(x0 + 0.005, y0 + 0.045),
                ha="center", fontsize=9, weight="bold", color=INK,
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.2), zorder=6)
    ax.set_xlim(area["lon_min"], area["lon_max"])
    ax.set_ylim(area["lat_min"], area["lat_max"])
    ax.set_aspect(1 / np.cos(np.radians(cam["lat"])))
    ax.set_xticks([])
    ax.set_yticks([])


def legend_levels(ax, loc="upper right"):
    handles = [Patch(fc=c, ec="none", label=n)
               for c, n in zip([NOT_SEEN] + LEVEL_COLORS, LEVEL_NAMES)]
    ax.legend(handles=handles, loc=loc, fontsize=8.5, frameon=True, framealpha=0.95,
              edgecolor="none", title="Camera can…", title_fontsize=8.5)


def main():
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    s3 = load_step("03_terrain_horizon.py")
    out = cfg["terrain"]["processed_dir"]
    fig_dir = "docs/images"
    os.makedirs(fig_dir, exist_ok=True)
    thr = cfg["dri"]
    eo = next(s for s in cfg["sensors"] if s["name"] == "EO_wide")
    table = dict(np.load(os.path.join(out, "horizon_table.npz")))
    dems = s3.load_dems(cfg["terrain"]["dem_dir"])
    area, cam = cfg["study_area"], cfg["camera"]
    to_utm = Transformer.from_crs("EPSG:4326", cfg["metric_crs"], always_xy=True)
    to_geo = Transformer.from_crs(cfg["metric_crs"], "EPSG:4326", always_xy=True)
    cx, cy = to_utm.transform(cam["lon"], cam["lat"])

    # Grid over the study area.
    glon, glat = np.meshgrid(np.linspace(area["lon_min"], area["lon_max"], 900),
                             np.linspace(area["lat_min"], area["lat_max"], 720))
    land = s3.sample_elevation(dems, glon, glat, cfg["terrain"]["sea_threshold_m"]) > 0
    hidden = s3.lookup_hidden(table, glon, glat, cfg["metric_crs"])
    gx, gy = to_utm.transform(glon, glat)
    dist = np.maximum(np.hypot(gx - cx, gy - cy), 1.0)

    def level_grid(height_m):
        visible = np.maximum(height_m - hidden, 0)
        px = visible * (eo["focal_mm"] / 1000) / (dist * eo["pixel_pitch_um"] * 1e-6)
        lvl = np.zeros(px.shape)
        lvl[px >= thr["detect_px"]] = 1
        lvl[px >= thr["recognize_px"]] = 2
        lvl[px >= thr["identify_px"]] = 3
        return np.where(land, np.nan, lvl)

    pts = pd.read_parquet(os.path.join(out, "dri_points.parquet"))
    mv = pts[pts["moving"]]

    # --- Figure 1: small craft vs cargo ship -------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(15, 7.4))
    for ax, (title, h) in zip(axes, [("Small craft · 2.5 m above water", 2.5),
                                     ("Cargo ship · 18 m above water", 18.0)]):
        ax.set_facecolor(LAND)
        ax.pcolormesh(glon, glat, level_grid(h), cmap=CMAP, norm=NORM, shading="auto",
                      zorder=1, rasterized=True)
        ax.contour(glon, glat, land.astype(float), levels=[0.5], colors=SEA_EDGE,
                   linewidths=0.5, zorder=2)
        sample = mv.sample(min(len(mv), 60_000), random_state=0)
        ax.scatter(sample["lon"], sample["lat"], s=0.15, c=INK, alpha=0.25, lw=0,
                   zorder=3, rasterized=True)
        decorate(ax, cfg, to_geo, cx, cy)
        ax.set_title(title, loc="left")
    legend_levels(axes[1])
    axes[0].scatter([], [], s=6, c=INK, alpha=0.6, label="Real AIS traffic (1 week)")
    axes[0].legend(loc="upper right", fontsize=8.5, frameon=True, framealpha=0.95,
                   edgecolor="none", markerscale=2)
    fig.suptitle("What can a shore camera actually see?  Day camera, wide setting "
                 f"({eo['focal_mm']} mm), mounted 30 m above sea level in Esbjerg",
                 x=0.012, ha="left", fontsize=14, weight="bold", color=INK)
    fig.text(0.012, 0.015, CREDIT, fontsize=7.5, color=INK_3)
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(os.path.join(fig_dir, "fig1_dri_zones.png"), dpi=170, facecolor="white")
    plt.close(fig)

    # --- Figure 2: real traffic coloured by best DRI level ------------------------
    fig, ax = plt.subplots(figsize=(9.5, 8.2))
    ax.set_facecolor("white")
    ax.contourf(glon, glat, land.astype(float), levels=[0.5, 1.5], colors=[LAND], zorder=1)
    ax.contour(glon, glat, land.astype(float), levels=[0.5], colors=SEA_EDGE,
               linewidths=0.5, zorder=2)
    order = mv.sort_values("BEST_central", ascending=False)   # draw 'not seen' on top
    ax.scatter(order["lon"], order["lat"], c=order["BEST_central"], cmap=CMAP, norm=NORM,
               s=0.6, lw=0, zorder=3, rasterized=True)
    decorate(ax, cfg, to_geo, cx, cy)
    legend_levels(ax)
    share = (mv["BEST_central"] == 0).mean()
    ax.set_title(f"One week of real traffic: {share:.0%} of moving-vessel positions "
                 "were not seen at all", loc="left", fontsize=12)
    fig.text(0.012, 0.012, CREDIT + "  |  best of day camera / thermal", fontsize=7,
             color=INK_3)
    fig.tight_layout(rect=(0, 0.025, 1, 1))
    fig.savefig(os.path.join(fig_dir, "fig2_traffic_seen.png"), dpi=170, facecolor="white")
    plt.close(fig)

    # --- Figure 3: recognized-or-better by class, with height sensitivity ---------
    bc = pd.read_csv(os.path.join(out, "dri_by_class.csv"))
    b = bc[(bc["sensor"] == "BEST") & (bc["vessel_class"] != "ALL")].copy()
    b = b.sort_values("recognize_or_better_central")
    nice = {"pleasure": "Pleasure craft", "fishing": "Fishing", "high_speed_craft":
            "High-speed craft", "service": "Tugs, pilots, service", "other": "Other / offshore",
            "tanker": "Tanker", "cargo": "Cargo", "unknown": "Unknown type",
            "passenger": "Passenger / ferry"}
    fig, ax = plt.subplots(figsize=(8.5, 5))
    y = np.arange(len(b))
    ax.barh(y, b["recognize_or_better_central"], height=0.55, color=LEVEL_COLORS[1], zorder=2)
    ax.errorbar(b["recognize_or_better_central"], y,
                xerr=[b["recognize_or_better_central"] - b["recognize_or_better_low"],
                      b["recognize_or_better_high"] - b["recognize_or_better_central"]],
                fmt="none", ecolor=INK_2, elinewidth=1, capsize=3, zorder=3)
    for yi, v in zip(y, b["recognize_or_better_central"]):
        ax.text(v + 1.5, yi + 0.27, f"{v:.0f}%", va="center", fontsize=8.5, color=INK)
    ax.set_yticks(y, [nice.get(c, c) for c in b["vessel_class"]])
    ax.tick_params(axis="y", labelcolor=INK)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of time the vessel is recognized or better (%)")
    ax.grid(axis="x", color="#e7e5df", zorder=0)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="y", length=0)
    val = b.set_index("vessel_class")["recognize_or_better_central"]
    ax.set_title(f"Recognized {val.get('cargo', float('nan')):.0f}% of the time for cargo "
                 f"ships, {val.get('pleasure', float('nan')):.0f}% for pleasure craft",
                 loc="left")
    fig.text(0.012, 0.015, "Bars: central height estimate. Whiskers: vessel heights "
             "-30 % / +30 %. Moving vessels only, best of day camera / thermal.",
             fontsize=7.5, color=INK_3)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(os.path.join(fig_dir, "fig3_by_class.png"), dpi=170, facecolor="white")
    plt.close(fig)
    print(f"Wrote {fig_dir}/fig1_dri_zones.png, fig2_traffic_seen.png, fig3_by_class.png")


if __name__ == "__main__":
    main()