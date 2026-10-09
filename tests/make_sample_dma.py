"""
Create a small synthetic AIS file in the DMA CSV layout, with deliberate
errors, to test scripts/01_prepare_ais.py without downloading real data.

Expected result after cleaning:
  - 3 vessels kept (cargo, fishing, pleasure); base station, bad MMSI and
    out-of-area vessel removed
  - duplicates and one position jump removed
  - the cargo vessel has 2 transits (60-minute silence in the middle)
  - the pleasure craft has no length -> imputed from its class

Usage:
    python tests/make_sample_dma.py data/raw/ais/sample_dma.csv
"""

import csv
import sys
from datetime import datetime, timedelta

HEADER = [
    "# Timestamp", "Type of mobile", "MMSI", "Latitude", "Longitude",
    "Navigational status", "ROT", "SOG", "COG", "Heading", "IMO", "Callsign",
    "Name", "Ship type", "Cargo type", "Width", "Length",
    "Type of position fixing device", "Draught", "Destination", "ETA",
    "Data source type", "A", "B", "C", "D",
]

T0 = datetime(2025, 6, 2, 8, 0, 0)


def row(t, mobile, mmsi, lat, lon, sog, name, ship_type, width, length):
    return [
        t.strftime("%d/%m/%Y %H:%M:%S"), mobile, mmsi, f"{lat:.6f}", f"{lon:.6f}",
        "Under way using engine", "", sog, "90", "90", "", "", name, ship_type,
        "", width, length, "GPS", "", "", "", "AIS", "", "", "", "",
    ]


def track(mmsi, name, ship_type, width, length, start, minutes, lat, lon0, dlon,
          mobile="Class A", sog="10"):
    out = []
    for i in range(minutes):
        out.append(row(start + timedelta(minutes=i), mobile, mmsi, lat,
                       lon0 + i * dlon, sog, name, ship_type, width, length))
    return out


def main(path):
    rows = []
    # Cargo ship: 20 min, silent 60 min, 20 more min -> 2 transits.
    rows += track(219000001, "NORDIC CARGO", "Cargo", 20, 150, T0, 20, 55.45, 8.00, 0.005)
    rows += track(219000001, "NORDIC CARGO", "Cargo", 20, 150,
                  T0 + timedelta(minutes=80), 20, 55.45, 8.20, 0.005)
    # Duplicate reception of the first cargo message (second shore station).
    rows.append(rows[0][:])
    # Fishing vessel with one impossible jump (~30 km in a minute).
    fish = track(219000002, "HAVFRUE", "Fishing", 6, 18, T0, 15, 55.50, 8.30, 0.001, sog="6")
    fish[7][3], fish[7][4] = "55.300100", "8.590000"
    rows += fish
    # Pleasure craft (Class B) with no length reported.
    rows += track(219000003, "SOMMER", "Pleasure", "", "", T0, 10, 55.47, 8.40, 0.0008,
                  mobile="Class B", sog="5")
    # Must be removed: base station, invalid MMSI, vessel outside the box.
    rows += track(2190999, "", "", "", "", T0, 5, 55.46, 8.43, 0, mobile="Base Station")
    rows += track(123, "BAD MMSI", "Cargo", 20, 120, T0, 5, 55.46, 8.10, 0.001)
    rows += track(219000004, "FAR AWAY", "Tanker", 30, 200, T0, 5, 56.50, 9.50, 0.001)

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/ais/sample_dma.csv")
