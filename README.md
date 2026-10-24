Ron Mathew
ronmathew@example.com
https://youtu.be/placeholder-video-link
python checker.py /path/to/data-pack

# Ridgeline Schools Weather Network: Data Quality Checker

A deterministic, evidence-based data-quality auditing tool designed for automated weather station (AWS) networks. Reads multi-station 10-minute meteorological time series, companion GPS NMEA logs, and volunteer maintenance notes, and produces an actionable quality flag manifest (`flags.csv`) and a non-technical network health summary (`report.html`).

---

## Architecture Overview

```
weather-checker/
├── checker.py              # CLI entry point (path discovery, pipeline coordination)
├── src/
│   ├── __init__.py
│   ├── ingestion.py        # Input discovery, schema validation, maintenance log parsing
│   ├── validation.py       # Structural, temporal, duplicate & gap checks
│   ├── anomalies.py        # Physical bounds, isolated spikes, flatlines, step changes
│   ├── gps.py              # NMEA GPRMC parsing, location discrepancy, clock offsets
│   ├── flag_builder.py     # Deterministic accumulator & redundant flag pruner
│   └── reporting.py        # One-page executive HTML report generator
├── tests/
│   └── test_checker.py     # 17 automated unit & edge-case tests
├── ai_logs/                # Genuine AI conversation logs and prompt records
├── DECISIONS.md            # Detailed answers to the 5 assignment questions
├── flags.csv               # Generated flag manifest
├── report.html             # Generated network health report
├── requirements.txt        # Minimal Python dependencies
└── README.md               # Documentation and execution instructions
```

---

## Installation & Setup

Requirements:
- Python 3.9+ (tested on Python 3.11, 3.12, 3.13)
- Only standard open-source scientific libraries: `pandas`, `numpy`, and `pytest`.

```bash
# Optional: create a virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install minimal dependencies
pip install -r requirements.txt
```

---

## Execution Instructions

### Standard Run
Supply the path to the unzipped data-pack folder as the positional argument:

```bash
python checker.py /path/to/data-pack
```

Example using the relative path from the repository root:
```bash
python checker.py ../dataset
```

### Specifying Output Directory
To write `flags.csv` and `report.html` to a specific destination folder:

```bash
python checker.py /path/to/data-pack --output-dir /path/to/output
```

Both outputs (`flags.csv` and `report.html`) are written to the target directory.

---

## Expected Input Structure

The checker dynamically discovers station data and does **not** hardcode station IDs:

```
data-pack/
├── stations.csv          # Metadata: station_id, name, lat, lon, elevation_m, installed, logger_interval_min
├── data/
│   ├── S01.csv           # 10-minute logger records: timestamp_utc, temp_c, rh_pct, pressure_hpa, ...
│   └── ...
├── gps/                  # Optional: NMEA 0183 sentences (one record per hour)
│   ├── S01.nmea
│   └── ...
└── maintenance_log.md    # Optional: Volunteer log entries (YYYY-MM-DD SID Note)
```

---

## Output Manifest: `flags.csv`

The output `flags.csv` conforms strictly to the 7 required columns:

| Column | Description |
|---|---|
| `station_id` | Identifier of the station (e.g. `S01`, `S02`, or custom ID) |
| `channel` | Specific sensor column (`temp_c`, `rh_pct`, etc.), `all`, or `metadata` |
| `start_utc` | Start timestamp of the affected interval (`YYYY-MM-DD HH:MM`) |
| `end_utc` | End timestamp of the affected interval (`YYYY-MM-DD HH:MM`) |
| `issue` | Taxonomy issue label (see table below) |
| `action` | Operational decision: `exclude`, `correct`, or `keep` |
| `note` | Evidence-based rationale including observed values, baselines, and justification |

### Issue Taxonomy

- `duplicate_timestamp`: Identical duplicate rows (`exclude`) or conflicting records with the same timestamp (`keep`).
- `missing_interval`: Continuous gaps exceeding expected 10-minute cadence (`exclude`).
- `invalid_numeric`: Unparseable numeric strings or corrupted sensor values (`exclude`).
- `physical_range_violation`: Values violating physical limits or ISA elevation-adjusted barometric bounds (`exclude`).
- `isolated_spike`: Single-point interior jump exceeding 5.0 MRZ with normal neighbors (`exclude`).
- `stuck_sensor`: $\ge 12$ consecutive identical readings ($\ge 2$ hours) on non-exempt channels (`exclude`).
- `abrupt_level_shift`: Persistent step change in median corroborated by sensor replacement notes (`correct` or `keep`).
- `post_maintenance_change`: Sustained distribution shift following hardware swap (`keep`).
- `clock_offset`: Systematic logger clock discrepancy vs. satellite GPS UTC exceeding 2 minutes (`keep`).
- `gps_location_mismatch`: Mean GPS coordinate differing from install sheet by $> 5$ km (`keep`).
- `gps_invalid_sentence`: Unparseable NMEA sentences or abnormal invalid fix rates (`keep`).

### Action Semantics

- **`exclude`**: Observations are demonstrably invalid, physically impossible, or corrupted by electrical/open-circuit faults. Must not be used in climatological analysis.
- **`correct`**: An identifiable systematic shift is present with a known physical root cause (e.g. uncalibrated replacement sensor) that can be re-zeroed against reference data.
- **`keep`**: Genuine extreme weather events or ambiguous anomalies where evidence is insufficient to justify data deletion. Retained with an explicit caveat.

---

## Running the Automated Test Suite

The test suite covers all 16 required edge-case scenarios, schema constraints, and determinism checks:

```bash
python -m pytest tests/test_checker.py -v
```

All 17 automated tests run locally in under 2 seconds without network or third-party dependencies.

---

## Determinism & Unseen Data Guarantee

- **No Hardcoded Station IDs**: Stations are discovered dynamically from `stations.csv` and the `data/` folder.
- **Deterministic Sorting**: Outputs are sorted stably by `(station_id, start_utc, channel)`.
- **Idempotency**: Repeated runs on identical inputs yield bit-identical `flags.csv` and `report.html` files.
