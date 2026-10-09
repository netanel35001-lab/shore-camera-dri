"""
Diagnostics for step 4 results (run after 04_dri.py).

1. Time zone check: traffic per hour of the day as written in the AIS files.
   If timestamps are UTC, the quietest hours fall around 01-03 (03-05 local
   summer time in Denmark, UTC+2). Pleasure craft peak in the afternoon.
2. IR-only recognitions: when (which hours) does IR recognize a vessel that
   EO_wide does not? That can only happen at "night" in the model.
3. Positions on DSM land cells: where are they, and are they moving?

Usage:
    python scripts/check_diagnostics.py
"""

import numpy as np
import pandas as pd

p = pd.read_parquet("data/processed/dri_points.parquet")
p["hour"] = pd.to_datetime(p["ts"]).dt.hour
mv = p[p["moving"]]
pd.set_option("display.width", 200)

print("1) Moving positions per hour of day (as written in the files)")
by_h = pd.DataFrame({
    "all_moving": mv.groupby("hour").size(),
    "pleasure": mv[mv["vessel_class"] == "pleasure"].groupby("hour").size(),
    "daylight_in_model": mv.groupby("hour")["daylight"].mean().round(2),
}).fillna(0)
print(by_h.to_string())

print("\n2) Moving positions recognized by IR but NOT by EO_wide, per hour")
ir_only = mv[(mv["IR_central"] >= 2) & (mv["EO_wide_central"] < 2)]
print(ir_only.groupby(["hour", "vessel_class"]).size().unstack(fill_value=0).to_string())

print("\n3) Positions on DSM land cells")
import importlib.util, yaml
spec = importlib.util.spec_from_file_location("s3", "scripts/03_terrain_horizon.py")
s3 = importlib.util.module_from_spec(spec); spec.loader.exec_module(s3)
cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
dems = s3.load_dems(cfg["terrain"]["dem_dir"])
z = s3.sample_elevation(dems, p["lon"].to_numpy(), p["lat"].to_numpy(),
                        cfg["terrain"]["sea_threshold_m"])
p["on_land"] = z > 0
p["dsm_m"] = z
print(f"   all positions on land: {p['on_land'].mean():.1%}   "
      f"moving positions on land: {p.loc[p['moving'], 'on_land'].mean():.1%}")
land_mv = p[p["on_land"] & p["moving"]].copy()
land_mv["cell"] = (land_mv["lat"].round(2).astype(str) + ", " +
                   land_mv["lon"].round(2).astype(str))
top = (land_mv.groupby("cell")
       .agg(positions=("mmsi", "size"), vessels=("mmsi", "nunique"),
            median_dsm_m=("dsm_m", "median"), km_from_camera=("dist_m", "median"))
       .sort_values("positions", ascending=False).head(12))
top["km_from_camera"] = (top["km_from_camera"] / 1000).round(1)
top["median_dsm_m"] = top["median_dsm_m"].round(1)
print("   Top ~1 km cells where MOVING vessels sit on 'land':")
print(top.to_string())