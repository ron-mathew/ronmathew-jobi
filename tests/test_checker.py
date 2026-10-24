"""
test_checker.py – comprehensive test suite for weather station data-quality checker.

Implements all 16 required test scenarios from the Master Build Prompt:
  1. Valid extreme temperature
  2. Impossible humidity reading
  3. Identical duplicate records
  4. Conflicting records with same timestamp
  5. Missing ten-minute intervals
  6. Isolated spike detection
  7. Prolonged constant sensor value (stuck sensor)
  8. Zero-MAD channel safety
  9. Malformed GPS sentence handling
  10. Valid GPS sentence with clock offset
  11. Nighttime zero irradiance exemption
  12. Real-looking rainfall event retention
  13. Maintenance-associated level shift
  14. Empty or malformed input folder handling
  15. Dataset with different station IDs
  16. Repeated runs producing identical output (determinism)
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure weather-checker root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.flag_builder import FlagBuilder, OUTPUT_COLUMNS, VALID_ACTIONS
from src.validation import check_structural
from src.anomalies import (
    check_physical_ranges,
    check_rh_overrange,
    check_isolated_spikes,
    check_stuck_sensor,
    check_abrupt_level_shift,
    check_rain_gauge_blockage,
)
from src.gps import check_gps
from src.ingestion import (
    find_data_pack,
    load_stations,
    discover_station_files,
    load_station_data,
    load_maintenance_log,
)


# ── Helper fixture builders ──────────────────────────────────────────────────

def _make_sample_df(n_rows: int = 30, start_time: str = "2025-03-01 00:00") -> pd.DataFrame:
    """Create a realistic baseline DataFrame with 10-minute intervals."""
    ts = pd.date_range(start_time, periods=n_rows, freq="10min")
    return pd.DataFrame({
        "timestamp_utc": ts,
        "temp_c": np.linspace(5.0, 10.0, n_rows),
        "rh_pct": np.linspace(50.0, 70.0, n_rows),
        "pressure_hpa": np.full(n_rows, 840.0),
        "wind_kmh": np.full(n_rows, 12.0),
        "wind_dir_deg": np.full(n_rows, 180.0),
        "rain_mm": np.zeros(n_rows),
        "ghi_wm2": np.zeros(n_rows),
    })


# ── 1. Valid extreme temperature ─────────────────────────────────────────────

def test_valid_extreme_temperature():
    """Valid cold temperature (-25°C at mountain station) should NOT be flagged."""
    df = _make_sample_df(30)
    df.loc[10, "temp_c"] = -25.0  # Cold but physically plausible for high Colorado altitude
    fb = FlagBuilder()
    check_physical_ranges("S01", df, fb, elevation_m=2950.0)

    flags = fb.to_dataframe()
    temp_flags = flags[flags["channel"] == "temp_c"]
    assert len(temp_flags) == 0, f"Expected no flags for -25°C, got: {temp_flags.to_dict()}"


# ── 2. Impossible humidity reading ───────────────────────────────────────────

def test_impossible_humidity():
    """RH > 100% or < 0% must be flagged and excluded."""
    df = _make_sample_df(30)
    df.loc[5:8, "rh_pct"] = 104.0  # Physical impossibility
    fb = FlagBuilder()
    check_rh_overrange("S05", df, fb)

    flags = fb.to_dataframe()
    assert len(flags) > 0
    rh_flag = flags.iloc[0]
    assert rh_flag["channel"] == "rh_pct"
    assert rh_flag["issue"] == "physical_range_violation"
    assert rh_flag["action"] == "exclude"
    assert "100%" in rh_flag["note"] or "overrange" in rh_flag["note"]


# ── 3. Identical duplicate records ───────────────────────────────────────────

def test_identical_duplicate_records():
    """Identical duplicate rows should be flagged for exclusion and deduplicated."""
    df = _make_sample_df(20)
    # Duplicate rows 5 to 7
    dups = df.iloc[5:8].copy()
    df_with_dups = pd.concat([df, dups]).reset_index(drop=True)

    fb = FlagBuilder()
    df_cleaned = check_structural("S08", df_with_dups, fb)

    flags = fb.to_dataframe()
    dup_flags = flags[flags["issue"] == "duplicate_timestamp"]
    assert len(dup_flags) == 1
    assert dup_flags.iloc[0]["action"] == "exclude"
    assert len(df_cleaned) == len(df), "Cleaned DataFrame should have duplicates removed"


# ── 4. Conflicting records with same timestamp ────────────────────────────────

def test_conflicting_duplicate_records():
    """Conflicting records with the same timestamp should be flagged as 'keep' with ambiguity note."""
    df = _make_sample_df(20)
    conflict_row = df.iloc[[10]].copy()
    conflict_row["temp_c"] = 25.0  # Different from original 7.6°C
    df_with_conflict = pd.concat([df, conflict_row]).reset_index(drop=True)

    fb = FlagBuilder()
    check_structural("S06", df_with_conflict, fb)

    flags = fb.to_dataframe()
    conf_flags = flags[flags["issue"] == "duplicate_timestamp"]
    assert len(conf_flags) == 1
    assert conf_flags.iloc[0]["action"] == "keep"
    assert "conflicting" in conf_flags.iloc[0]["note"].lower()


# ── 5. Missing ten-minute intervals ──────────────────────────────────────────

def test_missing_ten_minute_intervals():
    """A gap greater than 10 minutes should be flagged as missing_interval."""
    df = _make_sample_df(30)
    # Remove rows 10 through 20 (creating a 110-minute outage)
    df_with_gap = pd.concat([df.iloc[:10], df.iloc[20:]]).reset_index(drop=True)

    fb = FlagBuilder()
    check_structural("S08", df_with_gap, fb)

    flags = fb.to_dataframe()
    gap_flags = flags[flags["issue"] == "missing_interval"]
    assert len(gap_flags) == 1
    assert gap_flags.iloc[0]["action"] == "exclude"
    assert "absent" in gap_flags.iloc[0]["note"].lower() or "missing" in gap_flags.iloc[0]["note"].lower()


# ── 6. Isolated spike detection ──────────────────────────────────────────────

def test_isolated_spike():
    """A single isolated temperature jump (e.g. -40°C open circuit) should be flagged."""
    df = _make_sample_df(40)
    df.loc[20, "temp_c"] = -40.0  # Disconnection code in a series of +5 to +10°C
    fb = FlagBuilder()
    check_isolated_spikes("S03", df, fb)

    flags = fb.to_dataframe()
    spike_flags = flags[flags["issue"] == "isolated_spike"]
    assert len(spike_flags) == 1
    assert spike_flags.iloc[0]["channel"] == "temp_c"
    assert spike_flags.iloc[0]["action"] == "exclude"
    assert "-40.0" in spike_flags.iloc[0]["note"]


# ── 7. Prolonged constant sensor value (stuck sensor) ─────────────────────────

def test_stuck_sensor():
    """A temperature sensor constant for >= 12 intervals (2 hours) must be flagged as stuck_sensor."""
    df = _make_sample_df(40)
    df.loc[10:25, "temp_c"] = 6.5  # 16 consecutive identical readings

    fb = FlagBuilder()
    check_stuck_sensor("S03", df, fb)

    flags = fb.to_dataframe()
    temp_stuck = flags[(flags["issue"] == "stuck_sensor") & (flags["channel"] == "temp_c")]
    assert len(temp_stuck) == 1
    assert temp_stuck.iloc[0]["action"] == "exclude"


# ── 8. Zero-MAD channel safety ───────────────────────────────────────────────

def test_zero_mad_channel():
    """A constant series (MAD=0) should not cause divide-by-zero crashes."""
    df = _make_sample_df(30)
    df["temp_c"] = 15.0  # Perfectly constant, MAD == 0
    fb = FlagBuilder()

    # Must complete cleanly without Exception
    check_isolated_spikes("S01", df, fb)
    assert True


# ── 9. Malformed GPS sentence handling ───────────────────────────────────────

def test_malformed_gps_sentence(tmp_path):
    """Malformed or truncated NMEA lines should be handled gracefully without crashing."""
    gps_file = tmp_path / "S01.nmea"
    gps_file.write_text(
        "2025-03-01 00:00,$GPRMC,INVALID,CORRUPTED\n"
        "2025-03-01 01:00,NOT_AN_NMEA_SENTENCE\n"
        "2025-03-01 02:00,$GPRMC,020000.00,A,3945.2400,N,10508.7600,W,0.0,0.0,010325,,,A*60\n",
        encoding="utf-8",
    )
    fb = FlagBuilder()
    check_gps("S01", tmp_path, fb, install_lat=39.7540, install_lon=-105.1460)

    # Should not raise exception
    flags = fb.to_dataframe()
    # Malformed sentences note should be present
    mal_flags = flags[flags["issue"] == "gps_invalid_sentence"]
    assert len(mal_flags) >= 1


# ── 10. Valid GPS sentence with clock offset ─────────────────────────────────

def test_valid_gps_sentence_clock_offset(tmp_path):
    """When logger clock differs from GPS UTC by > 2 min, flag as clock_offset."""
    gps_file = tmp_path / "S06.nmea"
    # Logger says 10:00, but GPS says 16:00:00 UTC (6 hours ahead)
    lines = []
    for h in range(5):
        logger_ts = f"2025-03-24 {10+h:02d}:00"
        gps_time_str = f"{16+h:02d}0000.00"
        lines.append(f"{logger_ts},$GPRMC,{gps_time_str},A,3945.2400,N,10508.7600,W,0.0,0.0,240325,,,A*60")
    gps_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    fb = FlagBuilder()
    check_gps("S06", tmp_path, fb, install_lat=39.7540, install_lon=-105.1460)

    flags = fb.to_dataframe()
    clock_flags = flags[flags["issue"] == "clock_offset"]
    assert len(clock_flags) == 1
    assert clock_flags.iloc[0]["action"] == "keep"
    assert "6.00" in clock_flags.iloc[0]["note"] or "hour" in clock_flags.iloc[0]["note"]


# ── 11. Nighttime zero irradiance exemption ──────────────────────────────────

def test_nighttime_zero_irradiance():
    """GHI at 0.0 W/m² during nighttime must NOT be flagged as stuck_sensor."""
    df = _make_sample_df(30)
    df["ghi_wm2"] = 0.0  # Constant zero throughout night
    fb = FlagBuilder()
    check_stuck_sensor("S01", df, fb)

    flags = fb.to_dataframe()
    ghi_flags = flags[flags["channel"] == "ghi_wm2"]
    assert len(ghi_flags) == 0, "Nighttime zero GHI must be exempt from stuck sensor check"


# ── 12. Real-looking rainfall event retention ────────────────────────────────

def test_real_rainfall_event():
    """Rainfall during saturated conditions (RH=95%, GHI=20) should be retained, not excluded."""
    df = _make_sample_df(30)
    df.loc[15, "rain_mm"] = 6.0  # Significant rain
    df.loc[15, "rh_pct"] = 95.0   # Saturated, consistent with rain
    df.loc[15, "ghi_wm2"] = 25.0  # Overcast dark skies, consistent with rain

    fb = FlagBuilder()
    check_rain_gauge_blockage("S01", df, fb, maintenance_events=[])

    flags = fb.to_dataframe()
    rain_flags = flags[flags["channel"] == "rain_mm"]
    assert len(rain_flags) == 1
    assert rain_flags.iloc[0]["action"] == "keep", "Consistent rainfall conditions must be kept"


# ── 13. Maintenance-associated level shift ───────────────────────────────────

def test_maintenance_associated_level_shift():
    """Pressure board swap mentioned in maintenance notes triggers abrupt_level_shift."""
    # 100 observations before event, 100 observations after event
    ts = pd.date_range("2025-03-20 00:00", periods=200, freq="10min")
    p_before = np.full(100, 830.0)
    p_after = np.full(100, 840.4)  # Step shift of +10.4 hPa
    df = pd.DataFrame({
        "timestamp_utc": ts,
        "pressure_hpa": np.concatenate([p_before, p_after]),
    })
    event_ts = ts[100]  # Exact transition point
    events = [{
        "station_id": "S02",
        "date": event_ts,
        "note": "Swapped the pressure board with the spare from the cupboard",
    }]
    fb = FlagBuilder()
    check_abrupt_level_shift("S02", df, fb, maintenance_events=events)

    flags = fb.to_dataframe()
    shift_flags = flags[flags["issue"] == "abrupt_level_shift"]
    assert len(shift_flags) == 1
    assert shift_flags.iloc[0]["action"] == "correct"
    assert "+10.4" in shift_flags.iloc[0]["note"]


# ── 14. Empty or malformed input folder handling ─────────────────────────────

def test_empty_or_malformed_input_folder(tmp_path):
    """Calling find_data_pack on a non-existent or empty folder raises SystemExit with helpful message."""
    empty_dir = tmp_path / "empty_pack"
    empty_dir.mkdir()
    with pytest.raises(SystemExit) as exc:
        find_data_pack(empty_dir)
    assert "missing" in str(exc.value).lower()


# ── 15. Dataset with different station IDs ───────────────────────────────────

def test_different_station_ids(tmp_path):
    """Checker must dynamically discover stations with arbitrary IDs (e.g. X99, Z01)."""
    pack_dir = tmp_path / "custom_pack"
    pack_dir.mkdir()
    data_dir = pack_dir / "data"
    data_dir.mkdir()

    # Custom stations.csv
    stations_csv = pack_dir / "stations.csv"
    stations_csv.write_text(
        "station_id,name,lat,lon,elevation_m,installed,logger_interval_min\n"
        "X99,Summit Observatory,40.123,-105.456,3100,2024-01-01,10\n"
        "Z01,Valley Farm,39.999,-105.111,1500,2024-01-01,10\n",
        encoding="utf-8",
    )

    # Custom data files
    df_x99 = _make_sample_df(20)
    df_x99.to_csv(data_dir / "X99.csv", index=False)
    df_z01 = _make_sample_df(20)
    df_z01.to_csv(data_dir / "Z01.csv", index=False)

    s_csv, d_dir, g_dir, m_log = find_data_pack(pack_dir)
    st_df = load_stations(s_csv)
    files = discover_station_files(d_dir, st_df["station_id"].tolist())

    assert "X99" in files
    assert "Z01" in files
    assert len(files) == 2


# ── 16. Repeated runs producing identical output (determinism) ───────────────

def test_deterministic_output(tmp_path):
    """Multiple runs of FlagBuilder on identical inputs must produce identical CSV output."""
    def _run():
        fb = FlagBuilder()
        df = _make_sample_df(30)
        df.loc[5, "temp_c"] = -40.0
        df.loc[10:13, "rh_pct"] = 104.0
        check_isolated_spikes("S03", df, fb)
        check_rh_overrange("S05", df, fb)
        return fb.to_dataframe().to_csv(index=False)

    run1 = _run()
    run2 = _run()
    assert run1 == run2, "Output must be strictly deterministic across repeated runs"


# ── Schema and Action Integrity Tests ────────────────────────────────────────

def test_flags_csv_schema_and_actions():
    """Verify FlagBuilder enforces exactly the 7 required columns and valid actions."""
    fb = FlagBuilder()
    fb.add(
        station_id="S01",
        channel="temp_c",
        start_utc="2025-03-01 00:00",
        end_utc="2025-03-01 00:10",
        issue="isolated_spike",
        action="exclude",
        note="Valid test note",
    )
    df = fb.to_dataframe()

    assert list(df.columns) == OUTPUT_COLUMNS
    assert df.iloc[0]["action"] in VALID_ACTIONS

    # Invalid action should raise ValueError
    with pytest.raises(ValueError):
        fb.add(
            station_id="S01",
            channel="temp_c",
            start_utc="2025-03-01 00:00",
            end_utc="2025-03-01 00:10",
            issue="isolated_spike",
            action="invalid_action",
            note="Note",
        )
