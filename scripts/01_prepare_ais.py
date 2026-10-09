"""
Step 1 - Prepare AIS data.

Reads raw Danish Maritime Authority (DMA) AIS files, clips them to the study
area, cleans them and writes three outputs to data/processed/:

  ais_points.parquet   one row per cleaned position report (with transit_id)
  vessels.parquet      one row per MMSI with consolidated static data
  transits_summary.csv number of transits and vessels per vessel class

Usage:
    python scripts/01_prepare_ais.py            # uses config.yaml
    python scripts/01_prepare_ais.py --config other.yaml
"""

import argparse
import glob
import os
import sys
import zipfile

import duckdb
import yaml

# DMA "Ship type" text -> project vessel class.
# Classes are chosen by what matters for a camera: size and typical height.
SHIP_CLASS_MAP = {
    "cargo": "cargo",
    "tanker": "tanker",
    "passenger": "passenger",
    "fishing": "fishing",
    "hsc": "high_speed_craft",
    "pleasure": "pleasure",
    "sailing": "pleasure",
    "tug": "service",
    "towing": "service",
    "towing long/wide": "service",
    "pilot": "service",
    "port tender": "service",
    "sar": "service",
    "law enforcement": "service",
    "dredging": "service",
    "diving": "service",
    "anti-pollution": "service",
    "medical": "service",
    "military": "service",
    "wig": "other",
    "other": "other",
}

# Fallback hull length (m) when a class has no reported lengths at all in the
# study area. ASSUMPTION - typical values, documented in the README; only used
# when no vessel of that class reported a usable length.
DEFAULT_LENGTH_M = {
    "pleasure": 10, "fishing": 15, "service": 25, "high_speed_craft": 30,
    "passenger": 100, "cargo": 120, "tanker": 150, "other": 20, "unknown": 15,
}


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def collect_csv_files(raw_dir, extract_dir):
    """Return a list of CSV paths; DMA zips are extracted to extract_dir first."""
    os.makedirs(extract_dir, exist_ok=True)
    for zpath in glob.glob(os.path.join(raw_dir, "*.zip")):
        with zipfile.ZipFile(zpath) as zf:
            for member in zf.namelist():
                if member.lower().endswith(".csv"):
                    target = os.path.join(extract_dir, os.path.basename(member))
                    if not os.path.exists(target):
                        with zf.open(member) as src, open(target, "wb") as dst:
                            dst.write(src.read())
    files = glob.glob(os.path.join(raw_dir, "*.csv")) + glob.glob(
        os.path.join(extract_dir, "*.csv")
    )
    return sorted(set(files))


def class_case_sql():
    """Build a SQL CASE expression mapping lower-cased ship type to class."""
    whens = "\n".join(
        f"            WHEN '{k}' THEN '{v}'" for k, v in SHIP_CLASS_MAP.items()
    )
    return f"""CASE lower(trim(ship_type_raw))
{whens}
            ELSE 'unknown' END"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    area, ais = cfg["study_area"], cfg["ais"]

    files = collect_csv_files(ais["raw_dir"], os.path.join(ais["raw_dir"], "_extracted"))
    if not files:
        sys.exit(f"No AIS files found in {ais['raw_dir']}. Download DMA daily files first.")
    print(f"Found {len(files)} AIS file(s).")

    os.makedirs(ais["processed_dir"], exist_ok=True)
    con = duckdb.connect()

    # 1) Read raw files. DMA headers start with '# Timestamp'; normalize_names
    #    turns every header into snake_case so we do not depend on exact spelling.
    file_list = "[" + ", ".join(f"'{p}'" for p in files) + "]"
    con.execute(f"""
        CREATE TABLE raw AS
        SELECT * FROM read_csv({file_list},
            header = true, normalize_names = true, all_varchar = true,
            union_by_name = true, ignore_errors = true)
    """)
    cols = [r[0] for r in con.execute("DESCRIBE raw").fetchall()]

    def col(base):
        """Find a column by base name; DuckDB prefixes reserved words with '_'."""
        for c in cols:
            if c.strip("_") == base:
                return c
        sys.exit(f"Column '{base}' not found in AIS files. Columns: {cols}")

    ts_col = next(c for c in cols if "timestamp" in c)
    c_mmsi, c_lat, c_lon = col("mmsi"), col("latitude"), col("longitude")
    c_sog, c_cog, c_mob = col("sog"), col("cog"), col("type_of_mobile")
    c_name, c_type = col("name"), col("ship_type")
    c_len, c_wid = col("length"), col("width")
    n_raw = con.execute("SELECT count(*) FROM raw").fetchone()[0]
    print(f"Raw rows: {n_raw:,}")

    # 2) Clip to study area, keep ship transponders, cast types.
    mobile_list = ", ".join(f"'{m}'" for m in ais["mobile_types"])
    con.execute(f"""
        CREATE TABLE pts AS
        SELECT
            coalesce(try_strptime({ts_col}, '%d/%m/%Y %H:%M:%S'),
                     try_cast({ts_col} AS TIMESTAMP))        AS ts,
            try_cast(raw.{c_mmsi} AS BIGINT)                  AS mmsi,
            try_cast(raw.{c_lat} AS DOUBLE)                   AS lat,
            try_cast(raw.{c_lon} AS DOUBLE)                   AS lon,
            try_cast(raw.{c_sog} AS DOUBLE)                   AS sog_kn,
            try_cast(raw.{c_cog} AS DOUBLE)                   AS cog_deg,
            raw.{c_mob}                                       AS mobile_type,
            nullif(trim(raw.{c_name}), '')                    AS name,
            nullif(trim(raw.{c_type}), '')                    AS ship_type_raw,
            try_cast(raw.{c_len} AS DOUBLE)                   AS length_m,
            try_cast(raw.{c_wid} AS DOUBLE)                   AS width_m
        FROM raw
        WHERE raw.{c_mob} IN ({mobile_list})
          AND try_cast(raw.{c_lat} AS DOUBLE) BETWEEN {area['lat_min']} AND {area['lat_max']}
          AND try_cast(raw.{c_lon} AS DOUBLE) BETWEEN {area['lon_min']} AND {area['lon_max']}
    """)
    con.execute("DROP TABLE raw")

    # 3) Basic validity: ship MMSIs are 9 digits starting with 2-7 (MID range).
    con.execute("""
        DELETE FROM pts
        WHERE ts IS NULL OR mmsi IS NULL
           OR mmsi NOT BETWEEN 200000000 AND 799999999
    """)
    # 4) Exact duplicates (same vessel, same second) - several shore stations
    #    often receive the same message.
    con.execute("""
        CREATE TABLE pts_dedup AS
        SELECT * FROM pts
        QUALIFY row_number() OVER (PARTITION BY mmsi, ts ORDER BY lat) = 1
    """)
    n_clip = con.execute("SELECT count(*) FROM pts").fetchone()[0]
    n_dedup = con.execute("SELECT count(*) FROM pts_dedup").fetchone()[0]
    con.execute("DROP TABLE pts")

    # 5) Remove position spikes. A point is a spike when the implied speed both
    #    INTO it and OUT of it is impossible (it jumps away and comes back).
    #    Testing only the incoming speed would also drop the correct point
    #    right after the spike.
    max_kn = ais["max_implied_speed_kn"]
    hav = """2 * 6371000 * asin(sqrt(
                    pow(sin(radians({la2} - {la1}) / 2), 2) +
                    cos(radians({la1})) * cos(radians({la2})) *
                    pow(sin(radians({lo2} - {lo1}) / 2), 2)))"""
    con.execute(f"""
        CREATE TABLE pts_clean AS
        WITH nb AS (
            SELECT *,
                lag(lat) OVER w AS plat, lag(lon) OVER w AS plon, lag(ts) OVER w AS pts_,
                lead(lat) OVER w AS nlat, lead(lon) OVER w AS nlon, lead(ts) OVER w AS nts
            FROM pts_dedup
            WINDOW w AS (PARTITION BY mmsi ORDER BY ts)
        ), sp AS (
            SELECT *,
                {hav.format(la1='plat', lo1='plon', la2='lat', lo2='lon')}
                    / nullif(epoch(ts) - epoch(pts_), 0) * 1.943844 AS kn_in,
                {hav.format(la1='lat', lo1='lon', la2='nlat', lo2='nlon')}
                    / nullif(epoch(nts) - epoch(ts), 0) * 1.943844  AS kn_out
            FROM nb
        )
        SELECT * EXCLUDE (plat, plon, pts_, nlat, nlon, nts, kn_in, kn_out)
        FROM sp
        -- At a track end only one side exists; coalesce falls back to it.
        WHERE NOT (coalesce(kn_in, kn_out, 0) > {max_kn}
               AND coalesce(kn_out, kn_in, 0) > {max_kn})
    """)
    n_clean = con.execute("SELECT count(*) FROM pts_clean").fetchone()[0]
    con.execute("DROP TABLE pts_dedup")

    # 6) Transits: a new transit starts after a silence longer than the gap.
    gap_s = ais["transit_gap_min"] * 60
    con.execute(f"""
        CREATE TABLE pts_tr AS
        WITH flagged AS (
            SELECT *,
                CASE WHEN epoch(ts) - epoch(lag(ts) OVER w) > {gap_s}
                       OR lag(ts) OVER w IS NULL THEN 1 ELSE 0 END AS new_tr
            FROM pts_clean
            WINDOW w AS (PARTITION BY mmsi ORDER BY ts)
        )
        SELECT * EXCLUDE (new_tr),
            mmsi::VARCHAR || '_' ||
            sum(new_tr) OVER (PARTITION BY mmsi ORDER BY ts)::VARCHAR AS transit_id
        FROM flagged
    """)
    con.execute("DROP TABLE pts_clean")

    # 7) One static record per vessel: most frequent type, median plausible length.
    con.execute(f"""
        CREATE TABLE vessels AS
        SELECT
            mmsi,
            mode(name)          AS name,
            mode(mobile_type)   AS mobile_type,
            mode(ship_type_raw) AS ship_type_raw,
            median(CASE WHEN length_m BETWEEN {ais['length_min_m']} AND {ais['length_max_m']}
                        THEN length_m END) AS length_m,
            median(CASE WHEN width_m > 0 AND width_m < 80 THEN width_m END) AS width_m,
            count(*)            AS n_points,
            count(DISTINCT transit_id) AS n_transits
        FROM pts_tr
        GROUP BY mmsi
    """)
    con.execute(f"ALTER TABLE vessels ADD COLUMN vessel_class VARCHAR")
    con.execute(f"UPDATE vessels SET vessel_class = {class_case_sql()}")
    # Length source is kept per vessel so every number stays traceable:
    # reported -> class_median (same class in this data) -> class_default.
    defaults = " ".join(f"WHEN '{k}' THEN {v}" for k, v in DEFAULT_LENGTH_M.items())
    con.execute(f"""
        ALTER TABLE vessels ADD COLUMN length_source VARCHAR;
        UPDATE vessels SET length_source = 'reported' WHERE length_m IS NOT NULL;
        UPDATE vessels v SET length_m = c.med, length_source = 'class_median'
        FROM (SELECT vessel_class, median(length_m) AS med
              FROM vessels WHERE length_m IS NOT NULL GROUP BY vessel_class) c
        WHERE v.length_m IS NULL AND v.vessel_class = c.vessel_class;
        UPDATE vessels SET length_m = CASE vessel_class {defaults} ELSE 15 END,
                           length_source = 'class_default'
        WHERE length_m IS NULL;
    """)

    # 8) Write outputs.
    out = ais["processed_dir"]
    con.execute(f"""
        COPY (SELECT p.ts, p.mmsi, p.transit_id, p.lat, p.lon, p.sog_kn, p.cog_deg,
                     v.vessel_class, v.length_m
              FROM pts_tr p JOIN vessels v USING (mmsi)
              ORDER BY p.mmsi, p.ts)
        TO '{out}/ais_points.parquet' (FORMAT parquet)
    """)
    con.execute(f"COPY vessels TO '{out}/vessels.parquet' (FORMAT parquet)")
    con.execute(f"""
        COPY (SELECT vessel_class,
                     count(*)              AS vessels,
                     sum(n_transits)       AS transits,
                     round(median(length_m), 1) AS median_length_m,
                     sum((length_source <> 'reported')::INT) AS vessels_length_imputed
              FROM vessels GROUP BY vessel_class ORDER BY transits DESC)
        TO '{out}/transits_summary.csv' (HEADER)
    """)

    # 9) Data-quality log - this goes straight into the README.
    print("\nCleaning log")
    print(f"  in study area, ship transponders : {n_clip:,}")
    print(f"  after duplicate removal          : {n_dedup:,}")
    print(f"  after position-jump filter       : {n_clean:,}")
    print("\nTransits by vessel class")
    print(con.execute(f"SELECT * FROM read_csv('{out}/transits_summary.csv')").df().to_string(index=False))


if __name__ == "__main__":
    main()
