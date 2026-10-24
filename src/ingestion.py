"""
ingestion.py – load and validate raw input files from a data-pack folder.

Discovers station files dynamically; never hardcodes station IDs.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# Required columns for each station CSV (must match README schema)
REQUIRED_SENSOR_COLS: List[str] = [
    "timestamp_utc",
    "temp_c",
    "rh_pct",
    "pressure_hpa",
    "wind_kmh",
    "wind_dir_deg",
    "rain_mm",
    "ghi_wm2",
]

REQUIRED_STATION_COLS: List[str] = [
    "station_id",
    "name",
    "lat",
    "lon",
    "elevation_m",
    "installed",
    "logger_interval_min",
]

TS_FMT = "%Y-%m-%d %H:%M"


def find_data_pack(folder: Path) -> Tuple[Path, Path, Path, Path]:
    """
    Validate that *folder* looks like a data pack and return
    (stations_csv, data_dir, gps_dir, maintenance_log).
    Raises SystemExit with a helpful message on failure.
    """
    if not folder.is_dir():
        sys.exit(f"ERROR: data pack folder not found: {folder}")

    stations_csv = folder / "stations.csv"
    data_dir = folder / "data"
    gps_dir = folder / "gps"
    mlog = folder / "maintenance_log.md"

    missing = []
    if not stations_csv.exists():
        missing.append("stations.csv")
    if not data_dir.is_dir():
        missing.append("data/")
    if missing:
        sys.exit(
            f"ERROR: required files missing from {folder}: {missing}\n"
            "Expected: stations.csv, data/, gps/ (optional), maintenance_log.md (optional)"
        )
    return stations_csv, data_dir, gps_dir, mlog


def load_stations(stations_csv: Path) -> pd.DataFrame:
    """Load and validate stations.csv. Returns a clean DataFrame."""
    try:
        df = pd.read_csv(stations_csv)
    except Exception as exc:
        sys.exit(f"ERROR: cannot read {stations_csv}: {exc}")

    missing_cols = [c for c in REQUIRED_STATION_COLS if c not in df.columns]
    if missing_cols:
        sys.exit(
            f"ERROR: stations.csv is missing columns: {missing_cols}\n"
            f"Found columns: {list(df.columns)}"
        )

    # Parse installation dates
    df["installed"] = pd.to_datetime(df["installed"], errors="coerce")

    if df["station_id"].isnull().any():
        sys.exit("ERROR: stations.csv has rows with missing station_id.")

    df["station_id"] = df["station_id"].astype(str).str.strip()
    return df


def discover_station_files(data_dir: Path, station_ids: List[str]) -> Dict[str, Path]:
    """
    For each station_id in *station_ids*, look for a matching CSV in *data_dir*.
    Returns {station_id: path}.  Warns if a station has no file.
    """
    found: Dict[str, Path] = {}
    for sid in station_ids:
        candidate = data_dir / f"{sid}.csv"
        if candidate.exists():
            found[sid] = candidate
        else:
            print(f"WARNING: no data file for station {sid} in {data_dir}", file=sys.stderr)
    return found


def load_station_data(sid: str, path: Path) -> Optional[pd.DataFrame]:
    """
    Load a station CSV.  Returns None if the file is empty or unreadable.
    Timestamps are parsed; numeric coercion errors are reported.
    """
    if path.stat().st_size == 0:
        print(f"WARNING: {path} is empty – skipping {sid}", file=sys.stderr)
        return None

    try:
        df = pd.read_csv(path, dtype=str)
    except Exception as exc:
        print(f"WARNING: cannot read {path}: {exc}", file=sys.stderr)
        return None

    if df.empty:
        print(f"WARNING: {path} has no rows – skipping {sid}", file=sys.stderr)
        return None

    # Check required columns
    missing_cols = [c for c in REQUIRED_SENSOR_COLS if c not in df.columns]
    if missing_cols:
        print(
            f"WARNING: {sid} missing required columns {missing_cols} – skipping",
            file=sys.stderr,
        )
        return None

    # Parse timestamps
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], format=TS_FMT, errors="coerce")

    # Coerce numeric columns
    numeric_cols = [c for c in REQUIRED_SENSOR_COLS if c != "timestamp_utc"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["_station_id"] = sid
    return df


def load_maintenance_log(mlog: Path) -> List[dict]:
    """
    Parse maintenance_log.md into a list of events.
    Each event is {'date': pd.Timestamp, 'station_id': str, 'note': str}.
    Silently skips unparseable lines.
    """
    events: List[dict] = []
    if not mlog.exists():
        return events

    date_pattern = re.compile(
        r"^(\d{4}-\d{2}-\d{2})\s+(S\w+)\s+(.*)", re.IGNORECASE
    )
    try:
        with open(mlog, encoding="utf-8") as f:
            for line in f:
                m = date_pattern.match(line.strip())
                if m:
                    try:
                        dt = pd.Timestamp(m.group(1))
                        events.append(
                            {
                                "date": dt,
                                "station_id": m.group(2).strip(),
                                "note": m.group(3).strip(),
                            }
                        )
                    except Exception:
                        pass
    except Exception as exc:
        print(f"WARNING: cannot read {mlog}: {exc}", file=sys.stderr)

    return events
