# Shore Camera DRI — What Can a Coastal Camera Actually See?

**Question:** a shore-mounted EO/IR camera watches a port approach. What share of the real
vessel traffic can it *detect*, *recognize* and *identify* — and where are its blind spots?

**Approach:** real AIS traffic (Danish Maritime Authority, port of Esbjerg) is combined with a
geometric camera model: line of sight over terrain and structures, Earth curvature hiding the
lower part of distant hulls, and pixels-on-target against the Johnson criteria
(2 px detection / 8 px recognition / 12.8 px identification).

> Status: work in progress — Step 1 (AIS preparation) done.

## Pipeline

| Step | Script | Output |
|---|---|---|
| 1. Prepare AIS | `scripts/01_prepare_ais.py` | `ais_points.parquet`, `vessels.parquet`, `transits_summary.csv` |
| 2. Vessel height assumptions | — | `assumptions/vessel_heights.csv` |
| 3. Camera position and line of sight | — | viewshed raster |
| 4. DRI per AIS point / transit | — | DRI results table |
| 5. Maps | — | coverage, DRI rings, blind spots |
| 6. Second camera site | — | ranked candidate sites |

## Data

- **AIS:** Danish Maritime Authority historical AIS (free CSV download). Study window: one week.
- **Elevation:** Danish Elevation Model (DHM) — terrain and surface models.

## Step 1 — AIS preparation

Cleaning rules (all configurable in `config.yaml`):

1. Clip to the study-area bounding box; keep Class A and Class B transponders only.
2. Drop invalid MMSIs (ship MMSIs are 9 digits in the 2xx–7xx range).
3. Remove duplicate receptions (same MMSI, same timestamp).
4. Remove position spikes: points whose implied speed both into and out of them exceeds 50 kn.
5. Split each vessel's track into transits after a silence longer than 30 minutes.
6. One static record per vessel: most frequent ship type, median plausible length.
   Missing length is filled from the class median, then from a documented class default;
   `length_source` records which one was used.

## Run

```bash
pip install -r requirements.txt
# put DMA daily files (.zip or .csv) in data/raw/ais/
python scripts/01_prepare_ais.py
```

Test without real data:

```bash
python tests/make_sample_dma.py data/raw/ais/sample_dma.csv
python scripts/01_prepare_ais.py
```

## Known limitations

- Weather, haze and sea state are not modelled; DRI ranges are geometric best cases.
- Vessels without AIS are, by definition, not in the traffic baseline.
