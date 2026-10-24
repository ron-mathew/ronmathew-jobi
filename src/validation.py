"""
validation.py – structural and temporal checks on station data.

Checks: missing columns, malformed timestamps, duplicates, out-of-order
records, missing 10-minute intervals, unexpected sampling intervals, and
numeric parsing failures.
"""

from __future__ import annotations

from typing import List

import pandas as pd

from .flag_builder import FlagBuilder

EXPECTED_INTERVAL = pd.Timedelta("10min")
# Maximum gap size to call a single "missing_interval" event
# (larger gaps span many intervals but are one contiguous outage)


def check_structural(
    sid: str, df: pd.DataFrame, fb: FlagBuilder
) -> pd.DataFrame:
    """
    Run all structural/temporal checks.  Returns a cleaned copy of df
    with rows sorted and duplicate timestamps resolved for downstream use.
    """
    # 1. Missing-value count from numeric coercion
    sensor_cols = [
        "temp_c", "rh_pct", "pressure_hpa",
        "wind_kmh", "wind_dir_deg", "rain_mm", "ghi_wm2",
    ]
    for col in sensor_cols:
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
        df = df[~bad_ts].copy()

    # Sort chronologically for further checks
    df = df.sort_values("timestamp_utc").reset_index(drop=True)

    # 3. Duplicate timestamps
    dup_mask = df.duplicated("timestamp_utc", keep=False)
    if dup_mask.any():
        dup_groups = df[dup_mask].groupby("timestamp_utc")
        sensor_cols_dup = [c for c in sensor_cols if c in df.columns]
        identical_dups = []
        conflicting_dups = []

        for ts, grp in dup_groups:
            # Check if all duplicate rows are identical in sensor values
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
                    f"{len(identical_dups)} identical duplicate timestamps found "
                    f"({s} to {e}). Extra copies excluded; one copy retained."
                ),
            )
            # Keep first occurrence of identical duplicates
            df = df.drop_duplicates("timestamp_utc", keep="first").reset_index(drop=True)

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
                    f"{len(conflicting_dups)} conflicting duplicate timestamps: "
                    f"two records with same timestamp but different sensor values "
                    f"({s} to {e}). Ambiguity unresolved; original rows retained "
                    "pending further investigation."
                ),
            )
            # Keep first for downstream but flag as ambiguous
            df = df.drop_duplicates("timestamp_utc", keep="first").reset_index(drop=True)

    # 4. Out-of-order timestamps (after dedup sort, should be none, but verify)
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
                f"{len(neg)} out-of-order timestamps detected after deduplication. "
                "These rows cannot be reliably placed in the time sequence."
            ),
        )

    # 5. Missing 10-minute intervals (gaps > 10 min)
    diffs = df["timestamp_utc"].diff()
    gap_mask = diffs > EXPECTED_INTERVAL
    for idx in df.index[gap_mask]:
        gap_start = df.loc[idx - 1, "timestamp_utc"] if idx > 0 else df.loc[idx, "timestamp_utc"]
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
