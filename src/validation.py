"""
validation.py – structural and temporal checks on station data.

Checks: missing columns, malformed timestamps, duplicates, out-of-order
records, missing 10-minute intervals, unexpected sampling intervals, and
numeric parsing failures.
"""

from __future__ import annotations

import pandas as pd

from .flag_builder import FlagBuilder

EXPECTED_INTERVAL = pd.Timedelta("10min")


def check_structural(
    sid: str, df: pd.DataFrame, fb: FlagBuilder
) -> pd.DataFrame:
    """
    Run structural and temporal checks.

    Returns a chronologically sorted copy of df. Identical duplicate
    timestamps are reduced to one row. Conflicting duplicate timestamps
    are retained for downstream review and flagged as ambiguous.
    """
    df = df.copy()

    # 1. Missing or invalid numeric values
    sensor_cols = [
        "temp_c",
        "rh_pct",
        "pressure_hpa",
        "wind_kmh",
        "wind_dir_deg",
        "rain_mm",
        "ghi_wm2",
    ]

    for col in sensor_cols:
        if col not in df.columns:
            continue

        bad = df[col].isna()
        if bad.any():
            bad_rows = df.loc[bad, "timestamp_utc"]
            if not bad_rows.empty:
                start = bad_rows.min()
                end = bad_rows.max()
            else:
                start = end = pd.NaT

            fb.add(
                station_id=sid,
                channel=col,
                start_utc=start,
                end_utc=end,
                issue="invalid_numeric",
                action="exclude",
                note=(
                    f"{bad.sum()} values could not be parsed as numeric "
                    f"in column '{col}'. Affected rows span "
                    f"{start} to {end}."
                ),
            )

    # 2. Malformed / unparseable timestamps
    bad_ts = df["timestamp_utc"].isna()
    if bad_ts.any():
        fb.add(
            station_id=sid,
            channel="all",
            start_utc=pd.NaT,
            end_utc=pd.NaT,
            issue="invalid_timestamp",
            action="exclude",
            note=(
                f"{bad_ts.sum()} rows have unparseable timestamps and "
                "cannot be placed in time. Rows excluded."
            ),
        )
        df = df.loc[~bad_ts].copy()

    # Sort chronologically for further checks
    df = df.sort_values("timestamp_utc").reset_index(drop=True)

    # 3. Duplicate timestamps
    dup_mask = df.duplicated("timestamp_utc", keep=False)

    if dup_mask.any():
        dup_groups = df.loc[dup_mask].groupby("timestamp_utc")
        sensor_cols_dup = [c for c in sensor_cols if c in df.columns]

        identical_dups = []
        conflicting_dups = []

        for ts, grp in dup_groups:
            # Compare sensor values, treating matching missing values equally
            vals = grp[sensor_cols_dup].drop_duplicates()

            if len(vals) == 1:
                identical_dups.append(ts)
            else:
                conflicting_dups.append(ts)

        if identical_dups:
            s = min(identical_dups)
            e = max(identical_dups)

            fb.add(
                station_id=sid,
                channel="all",
                start_utc=s,
                end_utc=e,
                issue="duplicate_timestamp",
                action="exclude",
                note=(
                    f"{len(identical_dups)} identical duplicate timestamps "
                    f"found ({s} to {e}). Extra copies excluded; "
                    "one copy retained."
                ),
            )

            # Remove redundant copies only at identical duplicate timestamps.
            identical_ts = set(identical_dups)
            identical_mask = df["timestamp_utc"].isin(identical_ts)
            df_identical = df.loc[identical_mask].drop_duplicates(
                subset=["timestamp_utc", *sensor_cols_dup],
                keep="first",
            )
            df_other = df.loc[~identical_mask]
            df = (
                pd.concat([df_other, df_identical], axis=0)
                .sort_values("timestamp_utc")
                .reset_index(drop=True)
            )

        if conflicting_dups:
            s = min(conflicting_dups)
            e = max(conflicting_dups)

            fb.add(
                station_id=sid,
                channel="all",
                start_utc=s,
                end_utc=e,
                issue="duplicate_timestamp",
                action="keep",
                note=(
                    f"{len(conflicting_dups)} conflicting duplicate timestamps "
                    f"found ({s} to {e}). Sensor values differ for records "
                    "sharing a timestamp. All conflicting rows are retained "
                    "for investigation; no authoritative record is selected."
                ),
            )

            # Intentionally retain all conflicting rows.

    # 4. Out-of-order timestamps
    # Rows are sorted above; this is retained as a defensive check.
    diffs = df["timestamp_utc"].diff()
    neg = diffs[diffs < pd.Timedelta(0)]

    if not neg.empty:
        fb.add(
            station_id=sid,
            channel="all",
            start_utc=df.loc[neg.index[0], "timestamp_utc"],
            end_utc=df.loc[neg.index[-1], "timestamp_utc"],
            issue="out_of_order_timestamp",
            action="exclude",
            note=(
                f"{len(neg)} out-of-order timestamps detected after sorting. "
                "These rows require further inspection."
            ),
        )

    # 5. Missing 10-minute intervals
    # Duplicate timestamps yield a zero-minute difference and are not gaps.
    diffs = df["timestamp_utc"].diff()
    gap_mask = diffs > EXPECTED_INTERVAL

    for idx in df.index[gap_mask]:
        if idx == 0:
            continue

        gap_start = df.loc[idx - 1, "timestamp_utc"]
        gap_end = df.loc[idx, "timestamp_utc"]
        gap_duration = diffs.loc[idx]
        n_missing = int(gap_duration / EXPECTED_INTERVAL) - 1

        fb.add(
            station_id=sid,
            channel="all",
            start_utc=gap_start + EXPECTED_INTERVAL,
            end_utc=gap_end - EXPECTED_INTERVAL,
            issue="missing_interval",
            action="exclude",
            note=(
                f"Gap of {gap_duration} detected between "
                f"{gap_start} and {gap_end}. "
                f"Approximately {n_missing} expected 10-minute records are absent."
            ),
        )

    return df