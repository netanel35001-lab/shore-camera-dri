"""
Step 4 - What does the camera actually see? DRI for every AIS position.

For every cleaned AIS position (step 1) and every sensor in config.yaml:

  1. distance and hidden height from the horizon table (step 3)
  2. visible height  = vessel height (step 2) - hidden height
  3. pixels on target = visible height * focal length / (distance * pixel pitch)
  4. DRI level from the Johnson criteria: identify / recognize / detect / none

EO sensors only count in daylight (sun elevation above the threshold in
config.yaml); IR counts day and night. "Best available" combines them the
way an operator would use them: EO_wide or IR by day, IR at night.

All results are computed three times - with the central height estimate and
with the -30 % / +30 % height variants - so every number comes with its
sensitivity to the height assumption.

Also a data-consistency check: AIS positions that fall on DSM land cells.

Outputs (data/processed/):
  dri_points.parquet   DRI level per AIS position and sensor (central heights)
  dri_by_class.csv     share of moving-vessel positions per DRI level, by class
  dri_transits.csv     best DRI level reached per transit
Usage:
    python scripts/04_dri.py
"""

import importlib.util
import os

import numpy as np
import pandas as pd
import yaml

LEVELS = ["none", "detect", "recognize", "identify"]


def load_step3():
    """Reuse lookup_hidden / load_dems / sample_elevation from step 3."""
    path = os.path.join(os.path.dirname(__file__), "03_terrain_horizon.py")
    spec = importlib.util.spec_from_file_location("step3", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sun_elevation_deg(ts_utc, lat, lon):
    """Approximate solar elevation (NOAA formulas, error < ~1 deg).

    ts_utc: pandas datetime Series in UTC (DMA timestamps are UTC).
    """
    doy = ts_utc.dt.dayofyear.to_numpy()
    hour = (ts_utc.dt.hour + ts_utc.dt.minute / 60 + ts_utc.dt.second / 3600).to_numpy()
    g = 2 * np.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                       - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g)
            - 0.006758 * np.cos(2 * g) + 0.000907 * np.sin(2 * g)
            - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    tst = hour * 60 + eqtime + 4 * lon            # true solar time, minutes
    ha = np.radians(tst / 4 - 180)                # hour angle
    la = np.radians(lat)
    cos_zen = np.sin(la) * np.sin(decl) + np.cos(la) * np.cos(decl) * np.cos(ha)
    return 90 - np.degrees(np.arccos(np.clip(cos_zen, -1, 1)))


def dri_level(pixels, thr):
    lvl = np.zeros(pixels.shape, dtype="int8")
    lvl[pixels >= thr["detect_px"]] = 1
    lvl[pixels >= thr["recognize_px"]] = 2
    lvl[pixels >= thr["identify_px"]] = 3
    return lvl


def main():
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    s3 = load_step3()
    out = cfg["terrain"]["processed_dir"]
    thr, sensors = cfg["dri"], cfg["sensors"]

    pts = pd.read_parquet(os.path.join(out, "ais_points.parquet"))
    heights = pd.read_parquet(os.path.join(out, "vessels_with_height.parquet"),
                              columns=["mmsi", "height_m", "height_low_m", "height_high_m"])
    pts = pts.merge(heights, on="mmsi", how="left")
    table = dict(np.load(os.path.join(out, "horizon_table.npz")))
    print(f"{len(pts):,} AIS positions, {pts['mmsi'].nunique()} vessels")

    # Geometry: distance from camera and hidden height at each position.
    from pyproj import Transformer
    to_utm = Transformer.from_crs("EPSG:4326", cfg["metric_crs"], always_xy=True)
    cx, cy = to_utm.transform(cfg["camera"]["lon"], cfg["camera"]["lat"])
    x, y = to_utm.transform(pts["lon"].to_numpy(), pts["lat"].to_numpy())
    pts["dist_m"] = np.hypot(x - cx, y - cy)
    pts["hidden_m"] = s3.lookup_hidden(table, pts["lon"].to_numpy(), pts["lat"].to_numpy(),
                                       cfg["metric_crs"])

    # Daylight from sun elevation at the camera.
    ts = pd.to_datetime(pts["ts"])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    pts["sun_elev_deg"] = sun_elevation_deg(ts, cfg["camera"]["lat"], cfg["camera"]["lon"])
    pts["daylight"] = pts["sun_elev_deg"] > thr["daylight_sun_elev_deg"]
    # Consistency check: AIS positions on DSM land cells.
    dems = s3.load_dems(cfg["terrain"]["dem_dir"])
    z = s3.sample_elevation(dems, pts["lon"].to_numpy(), pts["lat"].to_numpy(),
                            cfg["terrain"]["sea_threshold_m"])
    pts["on_land"] = z > 0
    near = pts["dist_m"] < 2000
    print(f"\nConsistency check - AIS positions on DSM land cells: {pts['on_land'].mean():.2%}"
          f"  (within 2 km of the camera, i.e. quays/port basin: "
          f"{(pts['on_land'] & near).sum() / max(pts['on_land'].sum(), 1):.0%} of them)")

    # Land transponders: transits that mostly sit on land are not vessels at sea
    # (e.g. a boat on a trailer driving through town). Excluded from the results.
    land_share = pts.groupby("transit_id")["on_land"].mean()
    land_tr = land_share[land_share >= thr["land_transit_share"]].index
    pts["land_transponder"] = pts["transit_id"].isin(land_tr)

    # Moving = reported speed above the threshold AND real displacement over the
    # last few minutes. Moored Class B transponders report noisy 1-2 kn speeds
    # while staying put; the displacement test filters them out.
    # Seconds since epoch, independent of pandas' internal time unit.
    t = pd.to_datetime(pts["ts"]).to_numpy().astype("datetime64[s]").astype("int64")
    mmsi = pts["mmsi"].to_numpy().astype("int64")
    key = mmsi * 10**10 + t                       # rows are sorted by mmsi, ts
    i0 = np.searchsorted(key, key - thr["moving_window_s"], side="left")
    same = mmsi[i0] == mmsi
    dt = np.where(same, t - t[i0], 0)
    disp = np.hypot(x - x[i0], y - y[i0])
    has_window = same & (dt >= thr["moving_window_s"] / 2)
    really_moves = np.where(has_window, disp >= thr["moving_min_disp_m"], True)
    fast = pts["sog_kn"].fillna(0).to_numpy() >= thr["moving_min_sog_kn"]
    pts["moving"] = fast & really_moves & ~pts["land_transponder"].to_numpy()
    print(f"Filtered as moored speed jitter: {(fast & ~really_moves).sum():,} positions; "
          f"land transponders: {len(land_tr)} transit(s), "
          f"{pts['land_transponder'].sum():,} positions")

    # DRI per sensor and height variant.
    variants = {"central": "height_m", "low": "height_low_m", "high": "height_high_m"}
    dist = np.maximum(pts["dist_m"].to_numpy(), 1.0)
    for vname, hcol in variants.items():
        visible = np.maximum(pts[hcol].to_numpy() - pts["hidden_m"].to_numpy(), 0)
        for s in sensors:
            px = visible * (s["focal_mm"] / 1000) / (dist * s["pixel_pitch_um"] * 1e-6)
            lvl = dri_level(px, thr)
            if s["band"] == "eo":
                lvl = np.where(pts["daylight"], lvl, 0)   # EO is blind at night
            pts[f"{s['name']}_{vname}"] = lvl
            if vname == "central":
                pts[f"{s['name']}_px"] = px.astype("float32")
        # Operator's view: wide EO or IR by day, IR at night.
        pts[f"BEST_{vname}"] = np.maximum(pts[f"EO_wide_{vname}"], pts[f"IR_{vname}"])

    sensor_names = [s["name"] for s in sensors] + ["BEST"]
    keep = ["ts", "mmsi", "transit_id", "lat", "lon", "sog_kn", "vessel_class", "length_m",
            "height_m", "dist_m", "hidden_m", "daylight", "moving", "on_land",
            "land_transponder"] + \
           [f"{n}_central" for n in sensor_names] + [f"{s['name']}_px" for s in sensors]
    pts[keep].to_parquet(os.path.join(out, "dri_points.parquet"), index=False)

    # --- Summary 1: moving-vessel positions per DRI level, by class --------------
    mv = pts[pts["moving"]]
    rows = []
    for cls, g in list(mv.groupby("vessel_class")) + [("ALL", mv)]:
        for n in sensor_names:
            row = {"vessel_class": cls, "sensor": n, "positions": len(g)}
            for vname in variants:
                lv = g[f"{n}_{vname}"]
                row[f"recognize_or_better_{vname}"] = round((lv >= 2).mean() * 100, 1)
            lv = g[f"{n}_central"]
            for i, name in enumerate(LEVELS):
                row[f"{name}_pct"] = round((lv == i).mean() * 100, 1)
            rows.append(row)
    by_class = pd.DataFrame(rows)
    by_class.to_csv(os.path.join(out, "dri_by_class.csv"), index=False)

    # --- Summary 2: best level reached per transit (moving positions only) -------
    tr = (mv.groupby(["transit_id", "vessel_class"])
            [[f"{n}_central" for n in sensor_names]].max().reset_index())
    tr.to_csv(os.path.join(out, "dri_transits.csv"), index=False)

    # --- Report -------------------------------------------------------------------
    pd.set_option("display.width", 220)
    print(f"\nMoving positions: {len(mv):,} of {len(pts):,} "
          f"(moored/anchored reported apart); daylight share {mv['daylight'].mean():.0%}")
    print("\nShare of moving-vessel positions per DRI level - BEST available sensor (%)")
    best = by_class[by_class["sensor"] == "BEST"].set_index("vessel_class")
    print(best[[f"{l}_pct" for l in LEVELS]].to_string())
    print("\nRecognize-or-better (%) by sensor - central height [low / high]")
    view = by_class.pivot(index="vessel_class", columns="sensor",
                          values="recognize_or_better_central")[sensor_names]
    lo = by_class.pivot(index="vessel_class", columns="sensor",
                        values="recognize_or_better_low")[sensor_names]
    hi = by_class.pivot(index="vessel_class", columns="sensor",
                        values="recognize_or_better_high")[sensor_names]
    print((view.astype(str) + " [" + lo.astype(str) + "/" + hi.astype(str) + "]").to_string())
    print("\nTransits that were ever recognized or better (%) - by sensor")
    tsum = tr.groupby("vessel_class")[[f"{n}_central" for n in sensor_names]] \
             .apply(lambda g: (g >= 2).mean() * 100).round(1)
    tsum.columns = sensor_names
    tsum.loc["ALL"] = ((tr[[f"{n}_central" for n in sensor_names]] >= 2).mean() * 100).round(1).values
    print(tsum.to_string())
    print(f"\nWrote {out}/dri_points.parquet, dri_by_class.csv, dri_transits.csv")


if __name__ == "__main__":
    main()