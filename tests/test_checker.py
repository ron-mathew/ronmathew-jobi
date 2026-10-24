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
)


# ── Helper fixture builder ────────────────────────────────────────────────

def _make_sample_df(
    n_rows: int = 30,
    start_time: str = "2025-03-01 00:00",
) -> pd.DataFrame:
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


# ── 1. Valid extreme temperature ──────────────────────────────────────────

def test_valid_extreme_temperature():
    """Valid cold temperature (-25°C at mountain station) should not be flagged."""
    df = _make_sample_df(30)
    df.loc[10, "temp_c"] = -25.0
    fb = FlagBuilder()

    check_physical_ranges("S01", df, fb, elevation_m=2950.0)

    flags = fb.to_dataframe()
    temp_flags = flags[flags["channel"] == "temp_c"]
    assert len(temp_flags) == 0, (
        f"Expected no flags for -25°C, got: {temp_flags.to_dict()}"
    )


# ── 2. Impossible humidity ────────────────────────────────────────────────

def test_impossible_humidity():
    """RH above 100% must be flagged and excluded."""
    df = _make_sample_df(30)
    df.loc[5:8, "rh_pct"] = 104.0
    fb = FlagBuilder()

    check_rh_overrange("S05", df, fb)

    flags = fb.to_dataframe()
    assert len(flags) > 0

    rh_flag = flags.iloc[0]
    assert rh_flag["channel"] == "rh_pct"
    assert rh_flag["issue"] == "physical_range_violation"
    assert rh_flag["action"] == "exclude"
    assert "100%" in rh_flag["note"] or "overrange" in rh_flag["note"]


# ── 3. Identical duplicate records ────────────────────────────────────────

def test_identical_duplicate_records():
    """Identical duplicate rows should be flagged and deduplicated."""
    df = _make_sample_df(20)
    duplicates = df.iloc[5:8].copy()
    df_with_duplicates = pd.concat(
        [df, duplicates], ignore_index=True
    )

    fb = FlagBuilder()
    df_cleaned = check_structural("S08", df_with_duplicates, fb)

    flags = fb.to_dataframe()
    duplicate_flags = flags[flags["issue"] == "duplicate_timestamp"]

    assert len(duplicate_flags) == 1
    assert duplicate_flags.iloc[0]["action"] == "exclude"
    assert len(df_cleaned) == len(df)


# ── 4. Conflicting duplicate records ──────────────────────────────────────

def test_conflicting_duplicate_records():
    """Conflicting rows must be flagged and both retained after validation."""
    df = _make_sample_df(20)

    conflict_row = df.iloc[[10]].copy()
    conflict_timestamp = conflict_row["timestamp_utc"].iloc[0]
    original_temp = df.loc[10, "temp_c"]
    conflict_row.loc[:, "temp_c"] = 25.0

    df_with_conflict = pd.concat(
        [df, conflict_row], ignore_index=True
    )

    fb = FlagBuilder()
    df_cleaned = check_structural("S06", df_with_conflict, fb)

    flags = fb.to_dataframe()
    conflict_flags = flags[flags["issue"] == "duplicate_timestamp"]

    assert len(conflict_flags) == 1
    assert conflict_flags.iloc[0]["action"] == "keep"
    assert "conflicting" in conflict_flags.iloc[0]["note"].lower()

    retained = df_cleaned[
        df_cleaned["timestamp_utc"] == conflict_timestamp
    ]

    assert len(retained) == 2, (
        "Both rows sharing the conflicting timestamp must remain."
    )

    retained_temps = set(retained["temp_c"].tolist())
    assert original_temp in retained_temps
    assert 25.0 in retained_temps


# ── 5. Missing ten-minute intervals ───────────────────────────────────────

def test_missing_ten_minute_intervals():
    """A gap greater than 10 minutes should be flagged."""
    df = _make_sample_df(30)
    df_with_gap = pd.concat(
        [df.iloc[:10], df.iloc[20:]],
        ignore_index=True,
    )

    fb = FlagBuilder()
    check_structural("S08", df_with_gap, fb)

    flags = fb.to_dataframe()
    gap_flags = flags[flags["issue"] == "missing_interval"]

    assert len(gap_flags) == 1
    assert gap_flags.iloc[0]["action"] == "exclude"
    assert (
        "absent" in gap_flags.iloc[0]["note"].lower()
        or "missing" in gap_flags.iloc[0]["note"].lower()
    )


# ── 6. Isolated spike ─────────────────────────────────────────────────────

def test_isolated_spike():
    """An isolated -40°C temperature reading should be flagged."""
    df = _make_sample_df(40)
    df.loc[20, "temp_c"] = -40.0
    fb = FlagBuilder()

    check_isolated_spikes("S03", df, fb)

    flags = fb.to_dataframe()
    spike_flags = flags[flags["issue"] == "isolated_spike"]

    assert len(spike_flags) == 1
    assert spike_flags.iloc[0]["channel"] == "temp_c"
    assert spike_flags.iloc[0]["action"] == "exclude"
    assert "-40.0" in spike_flags.iloc[0]["note"]


# ── 7. Stuck sensor ───────────────────────────────────────────────────────

def test_stuck_sensor():
    """A temperature sensor constant for >=12 intervals should be flagged."""
    df = _make_sample_df(40)
    df.loc[10:25, "temp_c"] = 6.5
    fb = FlagBuilder()

    check_stuck_sensor("S03", df, fb)

    flags = fb.to_dataframe()
    stuck_flags = flags[
        (flags["issue"] == "stuck_sensor")
        & (flags["channel"] == "temp_c")
    ]

    assert len(stuck_flags) == 1
    assert stuck_flags.iloc[0]["action"] == "exclude"


# ── 8. Zero-MAD channel safety ───────────────────────────────────────────

def test_zero_mad_channel():
    """A constant series must not cause divide-by-zero errors."""
    df = _make_sample_df(30)
    df["temp_c"] = 15.0
    fb = FlagBuilder()

    check_isolated_spikes("S01", df, fb)


# ── 9. Malformed GPS sentence handling ───────────────────────────────────

def test_malformed_gps_sentence(tmp_path):
    """Malformed NMEA lines should be handled without crashing."""
    gps_file = tmp_path / "S01.nmea"
    gps_file.write_text(
        "2025-03-01 00:00,$GPRMC,INVALID,CORRUPTED\n"
        "2025-03-01 01:00,NOT_AN_NMEA_SENTENCE\n"
        "2025-03-01 02:00,$GPRMC,020000.00,A,3945.2400,N,"
        "10508.7600,W,0.0,0.0,010325,,,A*60\n",
        encoding="utf-8",
    )

    fb = FlagBuilder()
    check_gps(
        "S01",
        tmp_path,
        fb,
        install_lat=39.7540,
        install_lon=-105.1460,
    )

    flags = fb.to_dataframe()
    malformed_flags = flags[flags["issue"] == "gps_invalid_sentence"]
    assert len(malformed_flags) >= 1


# ── 10. Valid GPS sentence with clock offset ──────────────────────────────

def test_valid_gps_sentence_clock_offset(tmp_path):
    """A logger clock offset exceeding two minutes should be flagged."""
    gps_file = tmp_path / "S06.nmea"
    lines = []

    for hour in range(5):
        logger_timestamp = f"2025-03-24 {10 + hour:02d}:00"
        gps_time = f"{16 + hour:02d}0000.00"
        lines.append(
            f"{logger_timestamp},$GPRMC,{gps_time},A,"
            "3945.2400,N,10508.7600,W,0.0,0.0,240325,,,A*60"
        )

    gps_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    fb = FlagBuilder()
    check_gps(
        "S06",
        tmp_path,
        fb,
        install_lat=39.7540,
        install_lon=-105.1460,
    )

    flags = fb.to_dataframe()
    clock_flags = flags[flags["issue"] == "clock_offset"]

    assert len(clock_flags) == 1
    assert clock_flags.iloc[0]["action"] == "keep"
    assert (
        "6.00" in clock_flags.iloc[0]["note"]
        or "hour" in clock_flags.iloc[0]["note"].lower()
    )


# ── 11. Nighttime zero irradiance ────────────────────────────────────────

def test_nighttime_zero_irradiance():
    """Zero GHI during nighttime should not be flagged as a stuck sensor."""
    df = _make_sample_df(30)
    df["ghi_wm2"] = 0.0
    fb = FlagBuilder()

    check_stuck_sensor("S01", df, fb)

    flags = fb.to_dataframe()
    ghi_flags = flags[flags["channel"] == "ghi_wm2"]
    assert len(ghi_flags) == 0


# ── 12. Real-looking rainfall event ──────────────────────────────────────

def test_real_rainfall_event():
    """Rain with high humidity and low irradiance should be retained."""
    df = _make_sample_df(30)
    df.loc[15, "rain_mm"] = 6.0
    df.loc[15, "rh_pct"] = 95.0
    df.loc[15, "ghi_wm2"] = 25.0
    fb = FlagBuilder()

    check_rain_gauge_blockage("S01", df, fb, maintenance_events=[])

    flags = fb.to_dataframe()
    rain_flags = flags[flags["channel"] == "rain_mm"]

    assert len(rain_flags) == 1
    assert rain_flags.iloc[0]["action"] == "keep"


# ── 13. Maintenance-associated level shift ────────────────────────────────

def test_maintenance_associated_level_shift():
    """A pressure-board maintenance event should correlate with a level shift."""
    timestamps = pd.date_range(
        "2025-03-20 00:00", periods=200, freq="10min"
    )
    pressure_before = np.full(100, 830.0)
    pressure_after = np.full(100, 840.4)

    df = pd.DataFrame({
        "timestamp_utc": timestamps,
        "pressure_hpa": np.concatenate(
            [pressure_before, pressure_after]
        ),
    })

    event_timestamp = timestamps[100]
    maintenance_events = [{
        "station_id": "S02",
        "date": event_timestamp,
        "note": "Swapped the pressure board with the spare from the cupboard",
    }]

    fb = FlagBuilder()
    check_abrupt_level_shift(
        "S02", df, fb, maintenance_events=maintenance_events
    )

    flags = fb.to_dataframe()
    shift_flags = flags[flags["issue"] == "abrupt_level_shift"]

    assert len(shift_flags) == 1
    assert shift_flags.iloc[0]["action"] == "correct"
    assert "+10.4" in shift_flags.iloc[0]["note"]


# ── 14. Empty or malformed input folder ──────────────────────────────────

def test_empty_or_malformed_input_folder(tmp_path):
    """An empty data-pack folder should raise SystemExit with a helpful message."""
    empty_dir = tmp_path / "empty_pack"
    empty_dir.mkdir()

    with pytest.raises(SystemExit) as exc:
        find_data_pack(empty_dir)

    assert "missing" in str(exc.value).lower()


# ── 15. Different station IDs ────────────────────────────────────────────

def test_different_station_ids(tmp_path):
    """The checker should discover arbitrary station IDs dynamically."""
    pack_dir = tmp_path / "custom_pack"
    pack_dir.mkdir()
    data_dir = pack_dir / "data"
    data_dir.mkdir()

    stations_csv = pack_dir / "stations.csv"
    stations_csv.write_text(
        "station_id,name,lat,lon,elevation_m,installed,logger_interval_min\n"
        "X99,Summit Observatory,40.123,-105.456,3100,2024-01-01,10\n"
        "Z01,Valley Farm,39.999,-105.111,1500,2024-01-01,10\n",
        encoding="utf-8",
    )

    _make_sample_df(20).to_csv(data_dir / "X99.csv", index=False)
    _make_sample_df(20).to_csv(data_dir / "Z01.csv", index=False)

    stations_path, discovered_data_dir, _, _ = find_data_pack(pack_dir)
    stations_df = load_stations(stations_path)
    files = discover_station_files(
        discovered_data_dir,
        stations_df["station_id"].tolist(),
    )

    assert "X99" in files
    assert "Z01" in files
    assert len(files) == 2


# ── 16. Deterministic output ──────────────────────────────────────────────

def test_deterministic_output():
    """Repeated runs on identical inputs should produce identical CSV output."""
    def run_checks():
        fb = FlagBuilder()
        df = _make_sample_df(30)
        df.loc[5, "temp_c"] = -40.0
        df.loc[10:13, "rh_pct"] = 104.0

        check_isolated_spikes("S03", df, fb)
        check_rh_overrange("S05", df, fb)
        return fb.to_dataframe().to_csv(index=False)

    assert run_checks() == run_checks(), (
        "Output must be deterministic across repeated runs."
    )


# ── Schema and action integrity ──────────────────────────────────────────

def test_flags_csv_schema_and_actions():
    """FlagBuilder should enforce the required schema and valid actions."""
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