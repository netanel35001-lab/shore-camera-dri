"""
Quick look at the vessels of one or more classes (default: other, unknown).

Usage:
    python scripts/check_vessels.py
    python scripts/check_vessels.py fishing service
"""

import sys

import pandas as pd

classes = sys.argv[1:] or ["other", "unknown"]
v = pd.read_parquet("data/processed/vessels.parquet")
sel = v[v["vessel_class"].isin(classes)].sort_values("n_transits", ascending=False)
cols = ["mmsi", "name", "vessel_class", "ship_type_raw", "length_m", "length_source", "n_transits"]
pd.set_option("display.width", 200)
print(sel[cols].to_string(index=False))