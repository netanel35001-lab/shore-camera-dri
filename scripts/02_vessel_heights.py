"""
Step 2 - Vessel height assumptions.

The camera model needs the visible height of each vessel above the waterline
(its "critical dimension" for the Johnson criteria: in a side view the height
is the smaller dimension). AIS reports length but not height, so height is
estimated per vessel:

    height = clamp(height_ratio * length, h_min, h_max)

with ratio, min and max per vessel class from assumptions/vessel_heights.csv.

These are documented ENGINEERING ASSUMPTIONS, not measurements. To show how
much the results depend on them, two sensitivity variants are written too:
height_low (-30 %) and height_high (+30 %). Later steps report results for all
three, so a reader can see whether a conclusion survives the uncertainty.

Input : data/processed/vessels.parquet, assumptions/vessel_heights.csv
Output: data/processed/vessels_with_height.parquet

Usage:
    python scripts/02_vessel_heights.py
"""

import sys

import pandas as pd

SENSITIVITY = 0.30  # +/- 30 % around the central height estimate

vessels = pd.read_parquet("data/processed/vessels.parquet")
rules = pd.read_csv("assumptions/vessel_heights.csv")

missing = set(vessels["vessel_class"]) - set(rules["vessel_class"])
if missing:
    sys.exit(f"No height rule for class(es): {sorted(missing)}. "
             "Add them to assumptions/vessel_heights.csv.")

v = vessels.merge(rules[["vessel_class", "height_ratio", "h_min_m", "h_max_m"]],
                  on="vessel_class", how="left")
v["height_m"] = (v["height_ratio"] * v["length_m"]).clip(v["h_min_m"], v["h_max_m"])
v["height_low_m"] = v["height_m"] * (1 - SENSITIVITY)
v["height_high_m"] = v["height_m"] * (1 + SENSITIVITY)
v["height_clamped"] = ((v["height_ratio"] * v["length_m"]) != v["height_m"])

v = v.drop(columns=["height_ratio", "h_min_m", "h_max_m"])
v.to_parquet("data/processed/vessels_with_height.parquet", index=False)

summary = (v.groupby("vessel_class")
             .agg(vessels=("mmsi", "size"),
                  median_length_m=("length_m", "median"),
                  median_height_m=("height_m", "median"),
                  min_height_m=("height_m", "min"),
                  max_height_m=("height_m", "max"),
                  clamped=("height_clamped", "sum"))
             .round(1)
             .sort_values("median_height_m"))
pd.set_option("display.width", 200)
print("Estimated visible height above waterline, per vessel class")
print(summary.to_string())
print(f"\nSensitivity variants written: height_low_m (-{SENSITIVITY:.0%}), "
      f"height_high_m (+{SENSITIVITY:.0%})")