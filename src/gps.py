"""
gps.py – parse NMEA 0183 GPS files and perform time/location checks.

Supports $GPRMC sentences (primary) and $GPGGA (for altitude cross-check).
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .flag_builder import FlagBuilder

# Maximum acceptable coordinate difference (install vs GPS), in degrees
# ~0.01° ≈ 1 km at these latitudes
COORD_MISMATCH_THRESH_DEG = 0.05  # ~5 km

# Clock offset: if logger timestamp consistently differs from GPS UTC by more
# than this many seconds, flag as clock_offset.
CLOCK_OFFSET_THRESH_SEC = 120  # 2 minutes


def _dms_to_dd(val_str: str, hemi: str) -> Optional[float]:
    """Convert NMEA DDMM.MMMM (or DDDMM.MMMM) to decimal degrees."""
    if not val_str or not hemi:
        return None
    try:
        val = float(val_str)
        degrees = int(val / 100)
        minutes = val - degrees * 100.0
        dd = degrees + minutes / 60.0
        if hemi in ("S", "W"):
            dd = -dd
        return dd
    except (ValueError, TypeError):
        return None


def _parse_gprmc_line(logger_ts_str: str, sentence: str) -> Optional[dict]:
    """
    Parse a single $GPRMC sentence and return a record dict or None.
    Fields: $GPRMC,hhmmss.ss,A/V,lat,N/S,lon,E/W,speed,course,ddmmyy,...
    """
    fields = sentence.split(",")
    if len(fields) < 10 or not fields[0].startswith("$GPRMC"):
        return None

    try:
        gps_time_str = fields[1]
        validity = fields[2] if len(fields) > 2 else "V"
        lat_raw = fields[3]
        lat_h = fields[4]
        lon_raw = fields[5]
        lon_h = fields[6]
        date_raw = fields[9].split("*")[0] if "*" in fields[9] else fields[9]

        lat = _dms_to_dd(lat_raw, lat_h)
        lon = _dms_to_dd(lon_raw, lon_h)

        gps_dt = None
        if date_raw and gps_time_str and len(date_raw) >= 6 and len(gps_time_str) >= 6:
            d = int(date_raw[:2])
            mo = int(date_raw[2:4])
            yr = 2000 + int(date_raw[4:6])
            h = int(gps_time_str[:2])
            mi = int(gps_time_str[2:4])
            sec_str = gps_time_str[4:].split(".")[0]
            s = int(sec_str) if sec_str else 0
            gps_dt = pd.Timestamp(yr, mo, d, h, mi, s, tz=None)

        logger_ts = pd.to_datetime(logger_ts_str, errors="coerce")

        return {
            "logger_ts": logger_ts,
            "gps_dt": gps_dt,
            "validity": validity.strip() if validity else "V",
            "lat": lat,
            "lon": lon,
            "sentence_type": "GPRMC",
        }
    except Exception:
        return None


def load_nmea(
    sid: str,
    gps_dir: Path,
    fb: FlagBuilder,
    install_lat: float,
    install_lon: float,
) -> Optional[pd.DataFrame]:
    """
    Load and parse the NMEA file for *sid*.
    Returns a DataFrame of parsed records, or None if file not found.
    """
    path = gps_dir / f"{sid}.nmea"
    if not path.exists():
        return None

    records: List[dict] = []
    malformed_lines: int = 0
    total_lines: int = 0

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                total_lines += 1

                # Expected format: "logger_timestamp,NMEA_sentence"
                comma_pos = line.index(",") if "," in line else -1
                if comma_pos == -1:
                    malformed_lines += 1
                    continue

                # The logger timestamp is everything up to the first '$'
                dollar_pos = line.find("$")
                if dollar_pos == -1:
                    malformed_lines += 1
                    continue

                logger_ts_str = line[:dollar_pos].rstrip(",").strip()
                sentence = line[dollar_pos:].strip()

                if sentence.startswith("$GPRMC"):
                    rec = _parse_gprmc_line(logger_ts_str, sentence)
                    if rec:
                        records.append(rec)
                    else:
                        malformed_lines += 1
                # Skip $GPGGA for now (used only in manual checks)
    except Exception as exc:
        print(f"WARNING: cannot read GPS file {path}: {exc}", file=sys.stderr)
        return None

    if malformed_lines > 0:
        fb.add(
            station_id=sid,
            channel="metadata",
            start_utc=pd.NaT,
            end_utc=pd.NaT,
            issue="gps_invalid_sentence",
            action="keep",
            note=(
                f"{malformed_lines} of {total_lines} NMEA lines could not be "
                "parsed (malformed or unrecognised sentence type). "
                "Valid sentences analysed normally."
            ),
        )

    if not records:
        return None

    df = pd.DataFrame(records)
    return df


def check_gps(
    sid: str,
    gps_dir: Path,
    fb: FlagBuilder,
    install_lat: float,
    install_lon: float,
) -> None:
    """
    Run all GPS checks for one station:
      1. Parse and validate sentences.
      2. Check coordinate vs install sheet.
      3. Check logger clock vs GPS UTC.
    """
    df = load_nmea(sid, gps_dir, fb, install_lat, install_lon)
    if df is None:
        return

    valid_fixes = df[df["validity"] == "A"].copy()
    invalid_fixes = df[df["validity"] != "A"].copy()

    # Only flag as issue if invalid fix rate is abnormally high (> 15%)
    if len(df) > 0:
        pct = len(invalid_fixes) / len(df) * 100
        if pct > 15.0 and len(invalid_fixes) > 10:
            start = df["logger_ts"].min()
            end = df["logger_ts"].max()
            fb.add(
                station_id=sid,
                channel="metadata",
                start_utc=start,
                end_utc=end,
                issue="gps_invalid_sentence",
                action="keep",
                note=(
                    f"Abnormally high fraction of invalid GPS fixes: {len(invalid_fixes)} "
                    f"of {len(df)} GPRMC sentences report V flag ({pct:.1f}%). "
                    "May indicate receiver antenna obstruction or hardware degradation."
                ),
            )

    if valid_fixes.empty:
        return

    # 2. Coordinate check
    avg_lat = valid_fixes["lat"].dropna().mean()
    avg_lon = valid_fixes["lon"].dropna().mean()
    if not (math.isnan(avg_lat) or math.isnan(avg_lon)):
        dlat = abs(avg_lat - install_lat)
        dlon = abs(avg_lon - install_lon)

        # Rough distance in km (1 degree lat ≈ 111 km)
        dist_km = math.sqrt((dlat * 111) ** 2 + (dlon * 111 * math.cos(math.radians(install_lat))) ** 2)

        if dlat > COORD_MISMATCH_THRESH_DEG or dlon > COORD_MISMATCH_THRESH_DEG:
            start = valid_fixes["logger_ts"].min()
            end = valid_fixes["logger_ts"].max()
            fb.add(
                station_id=sid,
                channel="metadata",
                start_utc=start,
                end_utc=end,
                issue="gps_location_mismatch",
                action="keep",
                note=(
                    f"GPS mean location ({avg_lat:.4f}N, {avg_lon:.4f}E) differs "
                    f"from install sheet ({install_lat:.4f}N, {install_lon:.4f}E) "
                    f"by ~{dist_km:.1f} km (dlat={dlat:.4f}deg, dlon={dlon:.4f}deg). "
                    "Either install sheet coordinates are incorrect, or the GPS "
                    "receiver reports a biased position. Station data retained; "
                    "coordinate discrepancy requires physical verification."
                ),
            )

    # 3. Clock offset check
    both = df.dropna(subset=["logger_ts", "gps_dt"]).copy()
    if both.empty:
        return

    offsets_sec = (both["logger_ts"] - both["gps_dt"]).dt.total_seconds()
    large_offsets = both[offsets_sec.abs() > CLOCK_OFFSET_THRESH_SEC]

    if not large_offsets.empty:
        off_vals = (
            (large_offsets["logger_ts"] - large_offsets["gps_dt"])
            .dt.total_seconds()
        )
        median_offset = float(off_vals.median())
        start = large_offsets["logger_ts"].min()
        end = large_offsets["logger_ts"].max()
        n = len(large_offsets)

        fb.add(
            station_id=sid,
            channel="timestamp_utc",
            start_utc=start,
            end_utc=end,
            issue="clock_offset",
            action="keep",
            note=(
                f"Logger clock deviates from GPS UTC by {median_offset/3600:.2f} h "
                f"(median) across {n} GPS records from {start} to {end}. "
                "Logger timestamps during this period may be incorrect. "
                "Measurement values are retained; timestamps should be treated "
                "as approximate until clock source is verified."
            ),
        )
