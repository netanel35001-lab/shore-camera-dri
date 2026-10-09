"""
Step 6 - Where would a second camera help most?

1. Candidate sites: coastline cells (land next to sea) on a regular grid, thinned
   to one site per candidate_spacing_m and kept away from the existing camera.
2. For each site: the same line-of-sight model as step 3 (Earth curvature +
   terrain), for a camera on a mast of mast_height_m above the ground there.
3. For every moving AIS position: the best DRI level from the existing camera
   OR the candidate (day: EO_wide or IR, night: IR) - the operator uses both.
4. Sites are ranked by the share of time vessels are recognized or better.

Purely geometric: permits, power, access and ownership are NOT assessed. The
ranking says where a second camera adds the most coverage, not where it can be
built.

Outputs:
  data/processed/second_camera_ranking.csv
  docs/images/fig4_second_camera.png
Usage:
    python scripts/06_second_camera.py
"""

import importlib.util
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from pyproj import Transformer

EARTH_R = 6_371_000.0
SMALL = ["pleasure", "fishing"]


def load_step(name):
    path = os.path.join(os.path.dirname(__file__), name)
    spec = importlib.util.spec_from_file_location(name[:-3].replace(".", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def horizon_table(s3, dems, lon0, lat0, h_cam, cfg, az_step, rng_step):
    """Same calculation as step 3, for any camera position and height."""
    ter = cfg["terrain"]
    r_eff = EARTH_R / (1 - ter["refraction_k"])
    az = np.arange(0, 360, az_step)
    dist = np.arange(rng_step, ter["max_range_m"] + 1, rng_step)
    to_utm = Transformer.from_crs("EPSG:4326", cfg["metric_crs"], always_xy=True)
    to_geo = Transformer.from_crs(cfg["metric_crs"], "EPSG:4326", always_xy=True)
    cx, cy = to_utm.transform(lon0, lat0)
    a = np.radians(az)[:, None]
    lon, lat = to_geo.transform(cx + np.sin(a) * dist[None, :], cy + np.cos(a) * dist[None, :])
    z = s3.sample_elevation(dems, lon, lat, ter["sea_threshold_m"])
    z[:, dist < ter["skip_near_m"]] = 0
    drop = dist ** 2 / (2 * r_eff)
    ang = np.arctan2(z - drop[None, :] - h_cam, dist[None, :])
    mx = np.maximum.accumulate(ang, axis=1)
    mx = np.concatenate([np.full((len(az), 1), -np.pi / 2), mx[:, :-1]], axis=1)
    hidden = np.maximum(h_cam + dist[None, :] * np.tan(mx) + drop[None, :], 0)
    return dict(azimuth_deg=az, distance_m=dist, hidden_height_m=hidden.astype("float32"),
                camera_lon=lon0, camera_lat=lat0)


def candidate_sites(s3, dems, cfg):
    """Coastline cells on a metric grid, thinned to a minimum spacing."""
    sc, area = cfg["second_camera"], cfg["study_area"]
    to_utm = Transformer.from_crs("EPSG:4326", cfg["metric_crs"], always_xy=True)
    to_geo = Transformer.from_crs(cfg["metric_crs"], "EPSG:4326", always_xy=True)
    x0, y0 = to_utm.transform(area["lon_min"], area["lat_min"])
    x1, y1 = to_utm.transform(area["lon_max"], area["lat_max"])
    gx, gy = np.meshgrid(np.arange(x0, x1, sc["grid_step_m"]),
                         np.arange(y0, y1, sc["grid_step_m"]))
    glon, glat = to_geo.transform(gx, gy)
    z = s3.sample_elevation(dems, glon, glat, cfg["terrain"]["sea_threshold_m"])
    land = z > 0
    sea = ~land
    near_sea = np.zeros_like(land)
    near_sea[1:-1, 1:-1] = (sea[:-2, 1:-1] | sea[2:, 1:-1] | sea[1:-1, :-2] | sea[1:-1, 2:])
    coast = land & near_sea
    cx, cy = to_utm.transform(cfg["camera"]["lon"], cfg["camera"]["lat"])
    far = np.hypot(gx - cx, gy - cy) >= sc["min_distance_from_camera_m"]
    ix = np.argwhere(coast & far)
    chosen = []
    for r, c in ix:                                   # greedy thinning
        px, py = gx[r, c], gy[r, c]
        if all(np.hypot(px - qx, py - qy) >= sc["candidate_spacing_m"] for qx, qy, *_ in chosen):
            chosen.append((px, py, glon[r, c], glat[r, c], float(z[r, c])))
    return pd.DataFrame(chosen, columns=["x", "y", "lon", "lat", "ground_m"])


def best_level(s4, hidden, dist, height, daylight, sensors, thr):
    """Operator's best level for one camera: EO_wide (day only) or IR."""
    visible = np.maximum(height - hidden, 0)
    out = {}
    for s in sensors:
        if s["name"] not in ("EO_wide", "IR"):
            continue
        px = visible * (s["focal_mm"] / 1000) / (dist * s["pixel_pitch_um"] * 1e-6)
        lvl = s4.dri_level(px, thr)
        if s["band"] == "eo":
            lvl = np.where(daylight, lvl, 0)
        out[s["name"]] = lvl
    return np.maximum(out["EO_wide"], out["IR"])


def main():
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    s3, s4 = load_step("03_terrain_horizon.py"), load_step("04_dri.py")
    out = cfg["terrain"]["processed_dir"]
    sc, thr, sensors = cfg["second_camera"], cfg["dri"], cfg["sensors"]
    dems = s3.load_dems(cfg["terrain"]["dem_dir"])

    pts = pd.read_parquet(os.path.join(out, "dri_points.parquet"))
    mv = pts[pts["moving"]].reset_index(drop=True)
    lon, lat = mv["lon"].to_numpy(), mv["lat"].to_numpy()
    height = mv["height_m"].to_numpy()
    daylight = mv["daylight"].to_numpy()
    existing = mv["BEST_central"].to_numpy()
    small = mv["vessel_class"].isin(SMALL).to_numpy()
    to_utm = Transformer.from_crs("EPSG:4326", cfg["metric_crs"], always_xy=True)
    px_, py_ = to_utm.transform(lon, lat)

    base_all = (existing >= 2).mean() * 100
    base_small = (existing[small] >= 2).mean() * 100 if small.any() else np.nan
    print(f"Existing camera alone: recognized {base_all:.1f}% of the time "
          f"(small craft {base_small:.1f}%), {len(mv):,} moving positions")

    sites = candidate_sites(s3, dems, cfg)
    print(f"{len(sites)} candidate sites along the coast - computing line of sight ...")
    rows = []
    for k, s in sites.iterrows():
        h_cam = max(s["ground_m"], 0) + sc["mast_height_m"]
        tab = horizon_table(s3, dems, s["lon"], s["lat"], h_cam, cfg,
                            sc["azimuth_step_deg"], sc["range_step_m"])
        hidden = s3.lookup_hidden(tab, lon, lat, cfg["metric_crs"])
        dist = np.maximum(np.hypot(px_ - s["x"], py_ - s["y"]), 1.0)
        cand = best_level(s4, hidden, dist, height, daylight, sensors, thr)
        both = np.maximum(existing, cand)
        rows.append(dict(site=k + 1, lon=round(s["lon"], 5), lat=round(s["lat"], 5),
                         ground_m=round(s["ground_m"], 1), camera_asl_m=round(h_cam, 1),
                         recognized_all_pct=round((both >= 2).mean() * 100, 1),
                         recognized_small_pct=round((both[small] >= 2).mean() * 100, 1),
                         not_seen_pct=round((both == 0).mean() * 100, 1)))
        if (k + 1) % 10 == 0:
            print(f"  {k + 1}/{len(sites)} sites done", flush=True)
    rank = pd.DataFrame(rows)
    rank["gain_all_pts"] = (rank["recognized_all_pct"] - base_all).round(1)
    rank["gain_small_pts"] = (rank["recognized_small_pct"] - base_small).round(1)
    rank = rank.sort_values("recognized_all_pct", ascending=False).reset_index(drop=True)
    rank.to_csv(os.path.join(out, "second_camera_ranking.csv"), index=False)

    pd.set_option("display.width", 200)
    print("\nTop 5 sites (percentage points gained over the existing camera alone):")
    print(rank.head(5)[["site", "lat", "lon", "camera_asl_m", "recognized_all_pct",
                        "gain_all_pts", "recognized_small_pct", "gain_small_pts"]]
          .to_string(index=False))

    # --- Figure 4: candidate sites coloured by gain -------------------------------
    m5 = load_step("05_maps.py")
    area = cfg["study_area"]
    glon, glat = np.meshgrid(np.linspace(area["lon_min"], area["lon_max"], 700),
                             np.linspace(area["lat_min"], area["lat_max"], 560))
    land = s3.sample_elevation(dems, glon, glat, cfg["terrain"]["sea_threshold_m"]) > 0
    to_geo = Transformer.from_crs(cfg["metric_crs"], "EPSG:4326", always_xy=True)
    cx, cy = to_utm.transform(cfg["camera"]["lon"], cfg["camera"]["lat"])
    fig, ax = plt.subplots(figsize=(10.5, 8.2))
    ax.contourf(glon, glat, land.astype(float), levels=[0.5, 1.5], colors=[m5.LAND], zorder=1)
    ax.contour(glon, glat, land.astype(float), levels=[0.5], colors=m5.SEA_EDGE,
               linewidths=0.5, zorder=2)
    sample = mv.sample(min(len(mv), 60_000), random_state=0)
    ax.scatter(sample["lon"], sample["lat"], s=0.15, c=m5.INK, alpha=0.2, lw=0, zorder=2,
               rasterized=True)
    sc_ = ax.scatter(rank["lon"], rank["lat"], c=rank["gain_all_pts"], cmap="Blues",
                     vmin=0, vmax=max(rank["gain_all_pts"].max(), 1), s=46,
                     edgecolors=m5.INK_2, linewidths=0.6, zorder=5)
    best = rank.iloc[0]
    ax.plot(best["lon"], best["lat"], marker="*", ms=22, color=m5.LEVEL_COLORS[2],
            mec="white", mew=1.4, ls="none", zorder=7)
    ax.annotate(f"Best 2nd site\n+{best['gain_all_pts']:.0f} pts overall,"
                f" +{best['gain_small_pts']:.0f} pts small craft",
                (best["lon"], best["lat"]), xytext=(-14, -34), textcoords="offset points",
                ha="right", fontsize=8.5, weight="bold", color=m5.INK,
                bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="none", alpha=0.9), zorder=8)
    m5.decorate(ax, cfg, to_geo, cx, cy)
    fig.colorbar(sc_, ax=ax, shrink=0.55, pad=0.02, label="Gain in time recognized (percentage points)")
    ax.set_title(f"Where would a second camera help most?  {len(rank)} coastal sites tested",
                 loc="left")
    fig.text(0.012, 0.012, "Geometric coverage only - permits, power and access not assessed.  "
             f"Mast {sc['mast_height_m']} m.  Existing camera alone: {base_all:.0f}% recognized "
             f"(small craft {base_small:.0f}%).", fontsize=7.5, color=m5.INK_3)
    fig.tight_layout(rect=(0, 0.025, 1, 1))
    os.makedirs("docs/images", exist_ok=True)
    fig.savefig("docs/images/fig4_second_camera.png", dpi=170, facecolor="white")
    print("\nWrote data/processed/second_camera_ranking.csv and docs/images/fig4_second_camera.png")


if __name__ == "__main__":
    main()