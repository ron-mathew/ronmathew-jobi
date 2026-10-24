"""
anomalies.py – physical, statistical, and temporal anomaly detection.

Implements:
  A. Physical/semantic range checks (with elevation-aware pressure bounds)
  B. Isolated spikes (modified robust z-score)
  C. Stuck sensors (flatline detection)
  D. Abrupt level shifts (rolling median)
  E. Maintenance-correlated wind shift
  F. Rain gauge blockage spikes
"""

from __future__ import annotations

import math
from typing import List, Optional

import numpy as np
import pandas as pd

from .flag_builder import FlagBuilder


# ──────────────────────────────────────────────────────────────────────────────
# Elevation-aware pressure bounds (ISA standard atmosphere)
# ──────────────────────────────────────────────────────────────────────────────

def _isa_pressure(elevation_m: float) -> float:
    """Return ISA sea-level-corrected station pressure at *elevation_m* (hPa)."""
    return 1013.25 * (1.0 - 2.25577e-5 * elevation_m) ** 5.25588


def _pressure_bounds(elevation_m: float) -> tuple[float, float]:
    """
    Return (min, max) plausible pressure bounds for a station at *elevation_m*.
    Allow ±20% of the ISA nominal value to accommodate extreme weather events.
    """
    p_isa = _isa_pressure(elevation_m)
    return p_isa * 0.80, p_isa * 1.10


# ──────────────────────────────────────────────────────────────────────────────
# Static physical bounds
# ──────────────────────────────────────────────────────────────────────────────

TEMP_MIN = -50.0      # °C  (extreme cold possible at altitude)
TEMP_MAX = 50.0       # °C  (realistic Colorado upper bound)

RH_MIN = 0.0          # %
RH_MAX = 100.0        # %

WIND_MIN = 0.0        # km/h
WIND_MAX = 200.0      # km/h (very generous upper bound)

WIND_DIR_MIN = 0.0    # °
WIND_DIR_MAX = 360.0  # °

RAIN_MIN = 0.0        # mm

GHI_MIN = 0.0         # W/m²
GHI_MAX = 1400.0      # W/m²  (above theoretical surface max; catches encoding errors)

MRZ_THRESHOLD = 3.5   # modified robust z-score threshold for initial screening
SPIKE_MRZ = 5.0       # stricter threshold for isolated-spike detection
FLATLINE_MIN = 12     # consecutive identical readings to declare stuck sensor (= 2h)


def _mad(series: pd.Series) -> float:
    """Median absolute deviation."""
    med = series.median()
    return float((series - med).abs().median())


def _mrz(series: pd.Series) -> pd.Series:
    """
    Modified robust z-score: 0.6745 * (x - median) / MAD.
    Returns all-NaN series when MAD == 0 (constant channel).
    """
    med = series.median()
    mad_val = _mad(series)
    if mad_val == 0:
        return pd.Series(np.nan, index=series.index)
    return 0.6745 * (series - med) / mad_val


def _group_consecutive(indices: list, gap: int = 1) -> list:
    """Group a sorted list of integer indices into contiguous runs."""
    if not indices:
        return []
    groups, current = [], [indices[0]]
    for i in indices[1:]:
        if i - current[-1] <= gap:
            current.append(i)
        else:
            groups.append(current)
            current = [i]
    groups.append(current)
    return groups


# ──────────────────────────────────────────────────────────────────────────────
# A. Physical range checks (consolidated into intervals)
# ──────────────────────────────────────────────────────────────────────────────

def check_physical_ranges(
    sid: str,
    df: pd.DataFrame,
    fb: FlagBuilder,
    elevation_m: float = 0.0,
) -> None:
    """
    Flag observations that violate physical/instrument bounds.
    Adjacent violations of the same channel are consolidated into intervals.
    Pressure bounds are elevation-aware.
    RH > 100% is handled by check_rh_overrange() for consolidated interval reporting;
    this function skips RH to avoid double-flagging.
    """
    p_min, p_max = _pressure_bounds(elevation_m)

    bounds = {
        "temp_c":        (TEMP_MIN, TEMP_MAX, "°C", "Physical temperature limits for the region"),
        "pressure_hpa":  (p_min, p_max, "hPa",
                          f"ISA bounds at {elevation_m:.0f}m elevation "
                          f"(nominal {_isa_pressure(elevation_m):.0f} hPa ±20%)"),
        "wind_kmh":      (WIND_MIN, WIND_MAX, "km/h", "Wind speed is non-negative"),
        "wind_dir_deg":  (WIND_DIR_MIN, WIND_DIR_MAX, "°", "Direction 0–360°"),
        "ghi_wm2":       (GHI_MIN, GHI_MAX, "W/m²", "Irradiance is non-negative and capped at theoretical max"),
        "rain_mm":       (RAIN_MIN, float("inf"), "mm", "Interval accumulation is non-negative"),
    }
    # Note: rh_pct handled separately in check_rh_overrange()

    for col, (lo, hi, unit, reason) in bounds.items():
        if col not in df.columns:
            continue
        bad_mask = (df[col] < lo) | (df[col] > hi)
        bad_mask = bad_mask & df[col].notna()
        if not bad_mask.any():
            continue

        bad_idx = df.index[bad_mask].tolist()
        blocks = _group_consecutive(bad_idx)

        for block in blocks:
            rows = df.loc[block]
            start = rows["timestamp_utc"].min()
            end = rows["timestamp_utc"].max()
            vals = rows[col].tolist()
            n = len(block)
            val_summary = f"{min(vals):.2f}–{max(vals):.2f}" if n > 1 else f"{vals[0]:.2f}"

            fb.add(
                station_id=sid,
                channel=col,
                start_utc=start,
                end_utc=end,
                issue="physical_range_violation",
                action="exclude",
                note=(
                    f"{n} reading(s) of {col} outside valid range [{lo:.1f}, {hi:.1f}] {unit} "
                    f"({start} to {end}). Values: {val_summary} {unit}. "
                    f"Reason: {reason}. Observations excluded."
                ),
            )


def check_rh_overrange(sid: str, df: pd.DataFrame, fb: FlagBuilder) -> None:
    """
    Consolidate periods where RH > 100% into interval flags.
    Values 101–102% occurring persistently indicate sensor calibration drift
    or overrange, not genuine supersaturation.
    """
    over = df[df["rh_pct"] > RH_MAX].copy()
    if over.empty:
        return

    over_idx = over.index.tolist()
    blocks = _group_consecutive(over_idx, gap=3)  # allow small gaps in the run

    for block in blocks:
        rows = df.loc[block]
        start = rows["timestamp_utc"].min()
        end = rows["timestamp_utc"].max()
        n = len(block)
        vmin = rows["rh_pct"].min()
        vmax = rows["rh_pct"].max()
        duration_h = (end - start).total_seconds() / 3600

        fb.add(
            station_id=sid,
            channel="rh_pct",
            start_utc=start,
            end_utc=end,
            issue="physical_range_violation",
            action="exclude",
            note=(
                f"RH exceeds 100% for {n} readings over {duration_h:.1f} h "
                f"({start} to {end}). Range: {vmin:.0f}–{vmax:.0f}%. "
                "Values >100% are physically impossible for this sensor type. "
                "Indicates sensor overrange or calibration drift. "
                "Entire interval excluded."
            ),
        )


# ──────────────────────────────────────────────────────────────────────────────
# B. Isolated spike detection
# ──────────────────────────────────────────────────────────────────────────────

def check_isolated_spikes(sid: str, df: pd.DataFrame, fb: FlagBuilder) -> None:
    """
    Detect single-point spikes: one reading far from channel median, with
    normal neighbours before and after.
    """
    # Focus on channels where single-interval spikes represent instrument/electrical faults
    # (e.g. open-circuit or reset codes). Naturally intermittent variables (wind gusts, solar variations)
    # are excluded to prevent discarding legitimate weather events.
    spike_cols = ["temp_c", "pressure_hpa"]
    for col in spike_cols:
        series = df[col].copy()
        valid = series.dropna()
        if len(valid) < 20:
            continue
        scores = _mrz(valid)
        if scores.isna().all():
            continue

        med = float(valid.median())
        for idx in scores[scores.abs() > SPIKE_MRZ].index:
            val = df.loc[idx, col]
            # Skip values already violating physical bounds; physical_range_violation handles them
            if col == "temp_c" and not (TEMP_MIN <= val <= TEMP_MAX):
                continue
            if col == "pressure_hpa":
                p_min, p_max = _pressure_bounds(0.0)
                if not (p_min <= val <= p_max):
                    continue

            # Must be an interior point
            iloc_pos = df.index.get_loc(idx)
            if iloc_pos == 0 or iloc_pos >= len(df) - 1:
                continue
            prev_idx = df.index[iloc_pos - 1]
            next_idx = df.index[iloc_pos + 1]

            prev_score = abs(scores.get(prev_idx, 0)) if prev_idx in scores.index else 0
            next_score = abs(scores.get(next_idx, 0)) if next_idx in scores.index else 0

            # Both neighbours must be in normal range
            if prev_score < MRZ_THRESHOLD and next_score < MRZ_THRESHOLD:
                ts = df.loc[idx, "timestamp_utc"]
                fb.add(
                    station_id=sid,
                    channel=col,
                    start_utc=ts,
                    end_utc=ts,
                    issue="isolated_spike",
                    action="exclude",
                    note=(
                        f"Single-point spike: {col}={val:.1f} at {ts} "
                        f"(MRZ={scores[idx]:.1f}, threshold {SPIKE_MRZ}). "
                        f"Channel median={med:.2f}. Adjacent readings are normal. "
                        "Spike is isolated; observation excluded."
                    ),
                )


# ──────────────────────────────────────────────────────────────────────────────
# C. Stuck sensor detection
# ──────────────────────────────────────────────────────────────────────────────

def check_stuck_sensor(sid: str, df: pd.DataFrame, fb: FlagBuilder) -> None:
    """
    Flag channels that report identical values for >= FLATLINE_MIN consecutive
    10-minute readings (= 2 hours).

    Exempt from flagging:
    - wind_dir_deg (direction can genuinely be constant for hours)
    - rain_mm at 0.0 (dry periods are the norm)
    - ghi_wm2 at 0.0 (nighttime)
    """
    flatline_cols = {
        "temp_c": None,           # any constant value is suspicious
        "rh_pct": None,
        "pressure_hpa": None,
        "wind_kmh": 0.0,          # zero wind is common; only flag non-zero constant
    }

    for col, exempt_val in flatline_cols.items():
        if col not in df.columns:
            continue
        series = df[col].reset_index(drop=True)
        run_start = 0
        for i in range(1, len(series) + 1):
            at_end = i == len(series)
            changed = at_end or (series.iloc[i] != series.iloc[run_start])
            if changed:
                run_len = i - run_start
                val = series.iloc[run_start]
                # Skip exempt values
                if exempt_val is not None and val == exempt_val:
                    run_start = i
                    continue
                # Skip NaN runs
                if pd.isna(val):
                    run_start = i
                    continue

                if run_len >= FLATLINE_MIN:
                    start_ts = df.iloc[run_start]["timestamp_utc"]
                    end_ts = df.iloc[i - 1]["timestamp_utc"]
                    fb.add(
                        station_id=sid,
                        channel=col,
                        start_utc=start_ts,
                        end_utc=end_ts,
                        issue="stuck_sensor",
                        action="exclude",
                        note=(
                            f"{col} constant at {val} for {run_len} consecutive readings "
                            f"({start_ts} to {end_ts}, {run_len * 10} min). "
                            "Sensor appears frozen or reporting a cached value. "
                            "Interval excluded."
                        ),
                    )
                run_start = i


# ──────────────────────────────────────────────────────────────────────────────
# D. Abrupt level shift in pressure
# ──────────────────────────────────────────────────────────────────────────────

def check_abrupt_level_shift(
    sid: str,
    df: pd.DataFrame,
    fb: FlagBuilder,
    maintenance_events: Optional[list] = None,
) -> None:
    """
    Detect persistent step-changes in pressure_hpa using a before/after median
    comparison around each maintenance event that mentions a pressure sensor.

    Also performs a rolling-median scan for unlabelled shifts >= 5 hPa.
    """
    if "pressure_hpa" not in df.columns or len(df) < 144:
        return

    # --- Maintenance-event driven shift check ---
    if maintenance_events:
        for ev in maintenance_events:
            if ev["station_id"] != sid:
                continue
            note_lower = ev["note"].lower()
            if "pressure" not in note_lower and "barometer" not in note_lower:
                continue

            ev_ts = pd.Timestamp(ev["date"])
            before = df[df["timestamp_utc"] < ev_ts]["pressure_hpa"].dropna()
            after = df[df["timestamp_utc"] >= ev_ts]["pressure_hpa"].dropna()
            if len(before) < 30 or len(after) < 30:
                continue

            shift = after.median() - before.median()
            if abs(shift) >= 3.0:
                fb.add(
                    station_id=sid,
                    channel="pressure_hpa",
                    start_utc=ev_ts,
                    end_utc=df["timestamp_utc"].max(),
                    issue="abrupt_level_shift",
                    action="correct",
                    note=(
                        f"Pressure median shifted by {shift:+.1f} hPa at {ev_ts.date()} "
                        f"(before: {before.median():.1f} hPa, after: {after.median():.1f} hPa). "
                        f"Maintenance note: '{ev['note']}'. "
                        "New sensor not calibrated. The post-maintenance series may "
                        "carry a systematic offset. Correction requires calibration data "
                        "not supplied; operator must re-zero against a reference."
                    ),
                )

    # --- Rolling-median scan for unlabelled shifts ---
    df2 = df[["timestamp_utc", "pressure_hpa"]].copy().set_index("timestamp_utc").sort_index()
    roll = df2["pressure_hpa"].rolling("24h", min_periods=72).median()
    roll_diff = roll.diff()
    SHIFT_THRESH = 5.0
    shift_times = roll_diff[roll_diff.abs() >= SHIFT_THRESH]

    for ts, delta in shift_times.items():
        before_vals = df2.loc[:ts, "pressure_hpa"].tail(144).dropna()
        after_vals = df2.loc[ts:, "pressure_hpa"].head(144).dropna()
        if len(before_vals) < 10 or len(after_vals) < 10:
            continue
        actual_shift = after_vals.median() - before_vals.median()
        if abs(actual_shift) < SHIFT_THRESH:
            continue

        # Don't re-flag if already caught by maintenance event
        already_flagged = False
        if maintenance_events:
            for ev in maintenance_events:
                if ev["station_id"] == sid:
                    ev_ts = pd.Timestamp(ev["date"])
                    if abs((ev_ts - ts).total_seconds()) < 86400:
                        already_flagged = True
                        break
        if already_flagged:
            continue

        fb.add(
            station_id=sid,
            channel="pressure_hpa",
            start_utc=ts,
            end_utc=df["timestamp_utc"].max(),
            issue="abrupt_level_shift",
            action="keep",
            note=(
                f"Rolling-median pressure shift of {actual_shift:+.1f} hPa at {ts}. "
                f"No matching maintenance event. May reflect real weather or unlisted "
                "sensor event. Retained pending further investigation."
            ),
        )


# ──────────────────────────────────────────────────────────────────────────────
# E. Anemometer swap – wind distribution shift
# ──────────────────────────────────────────────────────────────────────────────

def check_wind_shift(
    sid: str,
    df: pd.DataFrame,
    fb: FlagBuilder,
    maintenance_events: Optional[list] = None,
) -> None:
    """
    Flag a persistent change in wind_kmh distribution at a maintenance event
    that mentions an anemometer replacement.
    """
    if maintenance_events is None or "wind_kmh" not in df.columns:
        return

    for ev in maintenance_events:
        if ev["station_id"] != sid:
            continue
        if not any(kw in ev["note"].lower() for kw in ("anemometer", "wind", "cup")):
            continue

        ev_ts = pd.Timestamp(ev["date"])
        before = df[df["timestamp_utc"] < ev_ts]["wind_kmh"].dropna()
        after = df[df["timestamp_utc"] >= ev_ts]["wind_kmh"].dropna()
        if len(before) < 30 or len(after) < 30:
            continue

        med_before = before.median()
        med_after = after.median()
        if med_before == 0:
            continue

        ratio = med_after / med_before
        if abs(ratio - 1) > 0.40:  # 40% relative change in median
            fb.add(
                station_id=sid,
                channel="wind_kmh",
                start_utc=ev_ts,
                end_utc=df["timestamp_utc"].max(),
                issue="post_maintenance_change",
                action="keep",
                note=(
                    f"Wind speed median: {med_before:.1f} km/h before → "
                    f"{med_after:.1f} km/h after anemometer swap on "
                    f"{ev['date'].date()} (ratio {ratio:.2f}). "
                    f"Note: '{ev['note']}'. "
                    "New anemometer brand may have different calibration. "
                    "Retained; pre/post periods should not be merged."
                ),
            )


# ──────────────────────────────────────────────────────────────────────────────
# F. Rain gauge blockage
# ──────────────────────────────────────────────────────────────────────────────

def check_rain_gauge_blockage(
    sid: str,
    df: pd.DataFrame,
    fb: FlagBuilder,
    maintenance_events: Optional[list] = None,
) -> None:
    """
    Flag large single-interval rain totals that are inconsistent with
    concurrent conditions (high irradiance, low humidity), suggesting
    accumulated debris release rather than actual precipitation.
    """
    RAIN_SPIKE_THRESH = 5.0  # mm / 10-min interval

    big_rain = df[df["rain_mm"] > RAIN_SPIKE_THRESH]
    if big_rain.empty:
        return

    for idx, row in big_rain.iterrows():
        ts = row["timestamp_utc"]
        val = row["rain_mm"]
        rh = row.get("rh_pct", float("nan"))
        ghi = row.get("ghi_wm2", float("nan"))

        # Rain should accompany high RH and low irradiance
        # If RH < 50% and GHI > 300 W/m², clearly not raining
        inconsistent = (not pd.isna(rh) and rh < 50) or (not pd.isna(ghi) and ghi > 300)

        corroboration = ""
        if maintenance_events:
            for ev in maintenance_events:
                if ev["station_id"] == sid:
                    ev_ts = pd.Timestamp(ev["date"])
                    if abs((ev_ts - ts).total_seconds()) < 86400 * 2:
                        if any(kw in ev["note"].lower() for kw in
                               ("rain", "gauge", "funnel", "leaves", "bird", "blocked", "cleaned")):
                            corroboration = f" Maintenance note {ev['date'].date()}: '{ev['note']}'."

        action = "exclude" if inconsistent else "keep"
        if inconsistent:
            reason = (
                f"Suspected rain gauge blockage release: apparent rainfall of {val:.1f} mm in 10 min "
                f"recorded under sunny, dry conditions (RH={rh:.0f}%, GHI={ghi:.0f} W/m²), physically "
                f"incompatible with real precipitation.{corroboration} "
                "Represents delayed drainage of trapped water or bucket agitation during funnel "
                "cleaning rather than meteorological rainfall; observation excluded."
            )
        else:
            reason = (
                f"Large rain interval ({val:.1f} mm in 10 min). Concurrent conditions "
                f"(RH={rh:.0f}%, GHI={ghi:.0f} W/m²) partially consistent with precipitation; "
                f"retained with caveat.{corroboration}"
            )

        fb.add(
            station_id=sid,
            channel="rain_mm",
            start_utc=ts,
            end_utc=ts,
            issue="isolated_spike",
            action=action,
            note=reason,
        )
