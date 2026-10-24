Ron Mathew  
ronmathew@example.com  
[Three-minute demonstration video](https://youtu.be/placeholder-video-link)  
`python checker.py /path/to/data-pack`

# Ridgeline Schools Weather Network: Data Quality Checker

A deterministic, evidence-based data-quality auditing tool for automatic weather station (AWS) networks. The checker processes multi-station meteorological time series, companion GPS NMEA logs, and volunteer maintenance notes to generate an actionable quality-flag manifest (`flags.csv`) and a non-technical network health report (`report.html`).

## Architecture Overview

```text
weather-checker/
├── checker.py
├── src/
│   ├── __init__.py
│   ├── ingestion.py
│   ├── validation.py
│   ├── anomalies.py
│   ├── gps.py
│   ├── flag_builder.py
│   └── reporting.py
├── tests/
│   └── test_checker.py
├── ai_logs/
├── DECISIONS.md
├── flags.csv
├── report.html
├── requirements.txt
└── README.md
```

- `checker.py` — CLI entry point and pipeline coordination.
- `src/ingestion.py` — Input discovery, schema validation, timestamp parsing, and maintenance-log parsing.
- `src/validation.py` — Structural validation, duplicate detection, chronological ordering, and missing-interval checks.
- `src/anomalies.py` — Physical-range checks, isolated spikes, stuck sensors, and maintenance-associated shifts.
- `src/gps.py` — GPS NMEA parsing, coordinate discrepancies, and logger-clock offsets.
- `src/flag_builder.py` — Centralized flag accumulation, interval consolidation, and redundant-flag handling.
- `src/reporting.py` — Generation of the executive HTML report.
- `tests/test_checker.py` — Automated tests for required edge cases, output constraints, and deterministic behavior.
- `ai_logs/` — Development-session AI conversation logs.
- `DECISIONS.md` — Engineering decisions and answers to the five assignment questions.

## Installation and Setup

**Requirements**

- Python 3.9 or later.
- Python dependencies declared in `requirements.txt`.
- `pytest` for running the test suite.

The implementation uses Python's standard library and the scientific/data libraries declared in the requirements file.

Create and activate a virtual environment if desired.

**Windows**

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**Linux/macOS**

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Execution Instructions

Run the checker from the repository root.

**Standard run**

```bash
python checker.py /path/to/data-pack
```

Example:

```bash
python checker.py ../dataset
```

**Specify an output directory**

```bash
python checker.py /path/to/data-pack --output-dir /path/to/output
```

The checker writes `flags.csv` and `report.html` to the selected output directory. If no output directory is specified, it uses the program's default output location.

## Expected Input Structure

```text
data-pack/
├── stations.csv
├── data/
│   ├── S01.csv
│   ├── S02.csv
│   └── ...
├── gps/
│   ├── S01.nmea
│   └── ...
└── maintenance_log.md
```

The checker discovers stations dynamically instead of hardcoding the supplied station IDs.

- `stations.csv` — Station metadata, including identifier, name, coordinates, elevation, installation date, and logging interval.
- `data/Sxx.csv` — Logger observations, including `timestamp_utc`, `temp_c`, `rh_pct`, `pressure_hpa`, `wind_kmh`, `wind_dir_deg`, `rain_mm`, and `ghi_wm2`.
- `gps/Sxx.nmea` — Logger timestamps paired with NMEA 0183 GPS sentences, including GPRMC and GPGGA where available.
- `maintenance_log.md` — Volunteer maintenance records.

GPS logs and maintenance notes may be absent where supported by the input contract. Missing or malformed optional files should not silently be interpreted as evidence that a station is healthy.

## Output Manifest: `flags.csv`

The flag manifest uses exactly seven columns.

| Column | Description |
|---|---|
| `station_id` | Station identifier |
| `channel` | Sensor channel, `all`, or `metadata` |
| `start_utc` | Start of the affected interval |
| `end_utc` | End of the affected interval |
| `issue` | Machine-readable issue category |
| `action` | Recommended operational action |
| `note` | Evidence-based rationale |

Timestamps use the `YYYY-MM-DD HH:MM` format.

### Issue taxonomy

| Issue | Meaning |
|---|---|
| `duplicate_timestamp` | Identical or conflicting records sharing a timestamp |
| `missing_interval` | Missing observations relative to the expected logging cadence |
| `invalid_numeric` | Unparseable or corrupted numeric values |
| `physical_range_violation` | Values outside configured physical limits, including elevation-aware pressure bounds |
| `isolated_spike` | An isolated sensor jump that meets the configured detection criteria |
| `stuck_sensor` | Repeated identical readings over the configured minimum duration |
| `abrupt_level_shift` | Persistent change potentially associated with maintenance |
| `post_maintenance_change` | Sustained distribution change following a hardware intervention |
| `clock_offset` | Logger time discrepancy relative to GPS UTC |
| `gps_location_mismatch` | GPS location inconsistent with installation metadata |
| `gps_invalid_sentence` | Malformed NMEA data or invalid GPS-fix evidence |

### Action semantics

- **`exclude`** — Observations are sufficiently supported as invalid or corrupted and should not be used without remediation.
- **`correct`** — A systematic error has an identifiable correction, subject to verification against suitable reference data.
- **`keep`** — Retain the observations while documenting uncertainty or a potential issue; do not discard valid extreme weather solely because it is unusual.

A flag records a quality finding and recommended action. It does not, by itself, mean that the underlying data have already been modified or that a proposed correction has been independently validated.

## Automated Testing

Run the test suite from the repository root:

```bash
python -m pytest tests/test_checker.py -v
```

The current reported test run contains **17 passing tests** covering 16 required edge-case scenarios, schema constraints, and deterministic output. The recorded run completed in **16.74 seconds** in the reported Windows/Python 3.13.5 environment. Runtime may vary across machines.

The tests cover cases including extreme but valid temperatures, impossible humidity, duplicate timestamps, missing intervals, isolated spikes, stuck sensors, GPS parsing, maintenance-associated shifts, empty or malformed input, dynamic station discovery, deterministic output, and flag-schema compliance.

## Determinism and Unseen Data

The implementation is designed to provide reproducible outputs for identical inputs.

- **Dynamic station discovery:** Station IDs are discovered from input metadata and available station data rather than being hardcoded.
- **Stable output ordering:** Flags are sorted consistently by station, start timestamp, and channel.
- **Deterministic processing:** Repeated runs on identical inputs should produce identical outputs, subject to the implementation's serialization and environment behavior.
- **No network dependency during normal processing:** The checker operates on the supplied local data pack and does not need to retrieve live weather observations.

The tool should be evaluated on additional station IDs and altered input conditions to assess generalization beyond the supplied dataset.

## Known Limitations

- An installation-sheet/GPS mismatch requires physical verification before the true station location can be confirmed.
- A pressure-board replacement may introduce a systematic offset, but a proposed correction should be checked against a reference barometer.
- A replacement anemometer with an unknown calibration curve may require the pre- and post-maintenance periods to be treated as separate calibration regimes.
- GPS-derived clock corrections require validation of the offset direction, affected time range, and duplicate-record handling before timestamps are changed.
- Automated quality flags are decision support. Ambiguous cases require documented engineering judgment.

## Dataset Licence and Attribution

The weather values are based on ERA5 reanalysis from the **Copernicus Climate Change Service**, accessed through the **Open-Meteo archive API**. The assignment identifies the data licence as **Creative Commons Attribution 4.0 International (CC BY 4.0)**.

Station names, people, hardware, and maintenance notes are fictional. The data pack is provided for this assignment only.

## Before Submission

- Replace the demonstration-video placeholder with the actual recording link.
- Confirm the contact details are correct.
- Run the checker against the supplied data pack and regenerate both output files.
- Run the complete test suite and retain the actual results.
- Review `DECISIONS.md` to ensure the engineering explanations match the final implementation.
- Check Git status and confirm that raw dataset files have not been committed.
