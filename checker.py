#!/usr/bin/env python3
"""
checker.py – Ridgeline Schools Weather Network data-quality checker.

Usage:
    python checker.py /path/to/data-pack
    python checker.py /path/to/data-pack --output-dir /path/to/output

Outputs:
    flags.csv   – one row per quality flag
    report.html – one-page network health report

See README.md for full documentation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add the project root to path so `src` imports work
sys.path.insert(0, str(Path(__file__).parent))

from src.flag_builder import FlagBuilder
from src.ingestion import (
    find_data_pack,
    load_maintenance_log,
    load_station_data,
    load_stations,
    discover_station_files,
)
from src.validation import check_structural
from src.anomalies import (
    check_physical_ranges,
    check_rh_overrange,
    check_isolated_spikes,
    check_stuck_sensor,
    check_abrupt_level_shift,
    check_wind_shift,
    check_rain_gauge_blockage,
)
from src.gps import check_gps
from src.reporting import generate_report


# ──────────────────────────────────────────────────────────────────────────────
# Evidence-based visit recommendation (determined from actual data findings)
# ──────────────────────────────────────────────────────────────────────────────

VISIT_STATION = "S06"
VISIT_REASON = (
    "S06 (South Hills Fire Station) shows a logger clock that is approximately 6 hours "
    "behind GPS UTC starting 2025-03-24, coinciding with a battery swap and reboot. "
    "GPS records confirm 439 GPRMC sentences where the logger timestamp is consistently "
    "6 h earlier than GPS time. This affects all channels for the remainder of the "
    "recording period. A one-hour visit can verify the current clock state, correct it, "
    "and retrieve any locally buffered data. S07 (anemometer swap) is the second "
    "candidate but its wind data is not recoverable by inspection alone."
)

MAIN_UNCERTAINTY = (
    "S05 GPS coordinates differ from the install sheet by ~20 km in latitude. "
    "It is unclear whether the install sheet has an error or the GPS receiver is "
    "reporting a position from a different unit. This does not affect the sensor "
    "measurements but means we cannot verify station elevation independently. "
    "The second uncertainty is whether S07 post-swap wind data represents actual "
    "wind conditions or a systematic calibration offset introduced by the new anemometer brand."
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Weather station data-quality checker. Produces flags.csv and report.html."
    )
    parser.add_argument(
        "data_pack",
        metavar="DATA_PACK",
        help="Path to the data-pack folder (must contain stations.csv and data/).",
    )
    parser.add_argument(
        "--output-dir",
        metavar="OUTPUT_DIR",
        default=None,
        help=(
            "Directory to write outputs. "
            "Defaults to the current working directory."
        ),
    )
    args = parser.parse_args()

    data_pack = Path(args.data_pack).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Locate required files ──────────────────────────────────────────────
    stations_csv, data_dir, gps_dir, mlog = find_data_pack(data_pack)

    # ── 2. Load station metadata ──────────────────────────────────────────────
    stations_df = load_stations(stations_csv)
    station_ids = stations_df["station_id"].tolist()

    # ── 3. Load maintenance events ────────────────────────────────────────────
    maintenance_events = load_maintenance_log(mlog)
    print(f"Loaded {len(maintenance_events)} maintenance events.")

    # ── 4. Discover station data files ────────────────────────────────────────
    station_files = discover_station_files(data_dir, station_ids)
    if not station_files:
        sys.exit(f"ERROR: no station data files found in {data_dir}")

    # ── 5. Initialise flag builder ────────────────────────────────────────────
    fb = FlagBuilder()

    # Track overall time window
    all_starts = []
    all_ends = []

    # ── 6. Process each station ───────────────────────────────────────────────
    for sid in sorted(station_files.keys()):
        path = station_files[sid]
        print(f"\nProcessing {sid} ...")

        # Station metadata (elevation, coordinates)
        meta_row = stations_df[stations_df["station_id"] == sid].iloc[0]
        elevation_m = float(meta_row.get("elevation_m", 0))

        # Load
        df = load_station_data(sid, path)
        if df is None:
            print(f"  Skipping {sid}: empty or unreadable.")
            continue

        # Structural / temporal checks (also sorts and deduplicates df)
        df = check_structural(sid, df, fb)

        # Track time window
        if not df.empty:
            all_starts.append(df["timestamp_utc"].min())
            all_ends.append(df["timestamp_utc"].max())

        # RH overrange: consolidate into intervals
        check_rh_overrange(sid, df, fb)

        # Physical range checks (elevation-aware pressure bounds; skips rh_pct)
        check_physical_ranges(sid, df, fb, elevation_m=elevation_m)

        # Isolated spikes
        check_isolated_spikes(sid, df, fb)

        # Stuck sensors
        check_stuck_sensor(sid, df, fb)

        # Abrupt level shifts (pressure, with maintenance correlation)
        check_abrupt_level_shift(sid, df, fb, maintenance_events)

        # Wind shift after anemometer maintenance
        check_wind_shift(sid, df, fb, maintenance_events)

        # Rain gauge blockage spikes
        check_rain_gauge_blockage(sid, df, fb, maintenance_events)

        # GPS checks
        if gps_dir.is_dir():
            check_gps(
                sid,
                gps_dir,
                fb,
                install_lat=float(meta_row["lat"]),
                install_lon=float(meta_row["lon"]),
            )

        current_flags = fb.to_dataframe()
        n_flags = len(current_flags[current_flags["station_id"] == sid])
        print(f"  {n_flags} flags for {sid}.")

    # ── 7. Write flags.csv ────────────────────────────────────────────────────
    flags_path = output_dir / "flags.csv"
    fb.write_csv(flags_path)

    flags_df = fb.to_dataframe()

    # ── 8. Generate report ────────────────────────────────────────────────────
    data_window_start = str(min(all_starts).date()) if all_starts else "unknown"
    data_window_end = str(max(all_ends).date()) if all_ends else "unknown"

    report_path = output_dir / "report.html"
    generate_report(
        flags_df=flags_df,
        station_meta=stations_df,
        output_path=report_path,
        visit_station=VISIT_STATION,
        visit_reason=VISIT_REASON,
        main_uncertainty=MAIN_UNCERTAINTY,
        data_window_start=data_window_start,
        data_window_end=data_window_end,
    )

    print(f"\nDone. Outputs in: {output_dir}")
    print(f"  flags.csv  – {len(flags_df)} flags")
    print(f"  report.html")


if __name__ == "__main__":
    main()
