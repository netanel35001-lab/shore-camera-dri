"""
Step 3 - Line of sight from the camera: Earth curvature + terrain.

For every direction (azimuth) and distance from the camera this step computes
the HIDDEN HEIGHT: how much of an object standing at sea level at that spot is
hidden from the camera. Anything taller than the hidden height is (partly)
visible; the visible part is  object height - hidden height.

Two things hide the lower part of an object:
  1. Earth curvature - the sea surface itself curves away below the camera's
     line of sight (with standard atmospheric refraction, k = 0.13).
  2. Terrain and structures between camera and object - land, dunes and
     buildings in the DSM (e.g. the island of Fano or the port itself).

Both are handled by ONE calculation: along each ray, every DEM cell (sea cells
are at 0 m) is converted to an elevation angle seen from the camera, after
lowering it by the curvature drop d^2 / (2 * R_eff). The running maximum of
that angle is the line that hides everything below it.

Outputs (data/processed/):
  horizon_table.npz     azimuths, distances, hidden height table  (used in step 4)
  hidden_height_map.png map: how tall must a vessel be to be seen here?

Usage:
    python scripts/03_terrain_horizon.py
"""

import glob
import os

import matplotlib
matplotlib.use("Agg")  # write files only, no window
import matplotlib.pyplot as plt
import numpy as np
import rasterio
import yaml
from pyproj import Transformer

EARTH_R = 6_371_000.0


def load_dems(dem_dir):
    """Open every readable GeoTIFF in dem_dir; skip broken downloads."""
    dems = []
    for path in sorted(glob.glob(os.path.join(dem_dir, "*.tif"))):
        try:
            src = rasterio.open(path)
        except rasterio.errors.RasterioIOError:
            print(f"  skipping {os.path.basename(path)} (not a valid raster - "
                  "probably an open-sea tile that does not exist)")
            continue
        dems.append((src.read(1).astype("float32"), src.transform, src.nodata,
                     src.bounds))
        print(f"  loaded {os.path.basename(path)}  bounds={tuple(round(b, 2) for b in src.bounds)}")
        src.close()
    return dems


def sample_elevation(dems, lon, lat, sea_threshold):
    """Elevation at many lon/lat points. Outside all tiles or no-data -> 0 (sea).

    Cells below sea_threshold are set to exactly 0: DSM noise on the water
    surface would otherwise act as a row of small 'walls' and hide targets.
    """
    z = np.zeros(lon.shape, dtype="float32")
    for arr, transform, nodata, b in dems:
        inside = (lon >= b.left) & (lon < b.right) & (lat > b.bottom) & (lat <= b.top)
        if not inside.any():
            continue
        col, row = ~transform * (lon[inside], lat[inside])
        row = np.clip(row.astype(int), 0, arr.shape[0] - 1)
        col = np.clip(col.astype(int), 0, arr.shape[1] - 1)
        vals = arr[row, col]
        if nodata is not None:
            vals = np.where(vals == nodata, 0, vals)
        z[inside] = vals
    return np.where(z < sea_threshold, 0, z)


def lookup_hidden(table, lon, lat, metric_crs):
    """Hidden height at arbitrary lon/lat points, from the horizon table.

    Nearest azimuth and distance bin; points beyond max range get +inf.
    Step 4 uses the same lookup for every AIS position.
    """
    to_utm = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    cx, cy = to_utm.transform(float(table["camera_lon"]), float(table["camera_lat"]))
    x, y = to_utm.transform(lon, lat)
    dx, dy = np.asarray(x) - cx, np.asarray(y) - cy
    d = np.hypot(dx, dy)
    a = np.degrees(np.arctan2(dx, dy)) % 360
    az, dist, hid = table["azimuth_deg"], table["distance_m"], table["hidden_height_m"]
    step_a = az[1] - az[0]
    step_d = dist[1] - dist[0]
    i = np.round(a / step_a).astype(int) % len(az)
    j = np.clip(np.round((d - dist[0]) / step_d).astype(int), 0, len(dist) - 1)
    out = hid[i, j].astype("float64")
    out[d > dist[-1]] = np.inf
    return out


def main():
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cam, ter = cfg["camera"], cfg["terrain"]
    out_dir = ter["processed_dir"]
    os.makedirs(out_dir, exist_ok=True)

    print("Loading elevation model ...")
    dems = load_dems(ter["dem_dir"])
    if not dems:
        raise SystemExit(f"No valid DEM tiles in {ter['dem_dir']}.")

    r_eff = EARTH_R / (1 - ter["refraction_k"])
    h_cam = float(cam["height_asl_m"])

    # Rays: azimuth (clockwise from north) x distance, in the metric CRS.
    az = np.arange(0, 360, ter["azimuth_step_deg"])
    dist = np.arange(ter["range_step_m"], ter["max_range_m"] + 1, ter["range_step_m"])
    to_utm = Transformer.from_crs("EPSG:4326", cfg["metric_crs"], always_xy=True)
    to_geo = Transformer.from_crs(cfg["metric_crs"], "EPSG:4326", always_xy=True)
    cx, cy = to_utm.transform(cam["lon"], cam["lat"])
    a = np.radians(az)[:, None]
    x = cx + np.sin(a) * dist[None, :]
    y = cy + np.cos(a) * dist[None, :]
    lon, lat = to_geo.transform(x, y)

    print(f"Sampling terrain along {len(az)} rays x {len(dist)} steps ...")
    z = sample_elevation(dems, lon, lat, ter["sea_threshold_m"])
    z[:, dist < ter["skip_near_m"]] = 0  # the camera's own building

    # Elevation angle of every cell, after the curvature drop.
    drop = dist ** 2 / (2 * r_eff)
    ang = np.arctan2(z - drop[None, :] - h_cam, dist[None, :])
    # Max angle of everything strictly BEFORE each distance hides the target.
    max_before = np.maximum.accumulate(ang, axis=1)
    max_before = np.concatenate([np.full((len(az), 1), -np.pi / 2), max_before[:, :-1]], axis=1)
    # Height above sea level of the hiding line at the target's distance.
    hidden = h_cam + dist[None, :] * np.tan(max_before) + drop[None, :]
    hidden = np.maximum(hidden, 0).astype("float32")

    table = dict(azimuth_deg=az, distance_m=dist, hidden_height_m=hidden,
                 camera_lon=cam["lon"], camera_lat=cam["lat"],
                 camera_height_m=h_cam, refraction_k=ter["refraction_k"])
    np.savez_compressed(os.path.join(out_dir, "horizon_table.npz"), **table)

    # --- Report -------------------------------------------------------------
    horizon_km = np.sqrt(2 * r_eff * h_cam) / 1000
    print(f"\nCamera: {cam['name']}")
    print(f"  height {h_cam:.0f} m ASL, sea horizon at {horizon_km:.1f} km (with refraction)")
    print("\nHidden height over open sea (curvature only), typical values:")
    for km in (5, 10, 15, 20, 25, 30):
        d = km * 1000
        h = max(0.0, (d - horizon_km * 1000) ** 2 / (2 * r_eff)) if d > horizon_km * 1000 else 0.0
        print(f"  {km:>2} km: {h:5.1f} m of the hull below the line of sight")
    blocked = (hidden[:, (dist >= 2000) & (dist <= 4000)] > 10).mean(axis=1) > 0.5
    print(f"\nDirections where land/structures hide >10 m already at 2-4 km: "
          f"{blocked.mean():.0%} of the 360 deg")

    # --- Map: how tall must a vessel be to be seen here? ----------------------
    # Regular lon/lat grid over the study area, filled from the horizon table.
    area = cfg["study_area"]
    glon, glat = np.meshgrid(np.linspace(area["lon_min"], area["lon_max"], 650),
                             np.linspace(area["lat_min"], area["lat_max"], 520))
    gz = sample_elevation(dems, glon, glat, ter["sea_threshold_m"])
    ghid = lookup_hidden(table, glon, glat, cfg["metric_crs"])
    land = gz > 0
    shown = np.where(land, np.nan, ghid)
    fig, ax = plt.subplots(figsize=(9, 8))
    pc = ax.pcolormesh(glon, glat, shown, cmap="viridis_r", vmin=0, vmax=40, shading="auto")
    ax.contourf(glon, glat, land.astype(float), levels=[0.5, 1.5], colors=["0.85"])
    ax.contour(glon, glat, land.astype(float), levels=[0.5], colors="0.35", linewidths=0.6)
    ax.plot(cam["lon"], cam["lat"], marker="*", color="red", markersize=14, label="camera",
            linestyle="none")
    ax.set_xlim(area["lon_min"], area["lon_max"])
    ax.set_ylim(area["lat_min"], area["lat_max"])
    ax.set_aspect(1 / np.cos(np.radians(cam["lat"])))
    ax.set_title(f"Hidden height: how tall must a vessel be to be seen?\n"
                 f"camera {h_cam:.0f} m ASL, Earth curvature + terrain (Copernicus GLO-30)")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    fig.colorbar(pc, ax=ax, label="Hidden height above sea level (m)")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "hidden_height_map.png"), dpi=150)
    print(f"\nWrote {out_dir}/horizon_table.npz and {out_dir}/hidden_height_map.png")


if __name__ == "__main__":
    main()