# Ridgeline Schools Weather Network: Data Quality Checker

**Author:** Ron Mathew Jobi
**Contact:** ronmathew2210@gmail.com
**Demo:** [Watch the three-minute demonstration](https://youtu.be/Pl7q4AyhxJ0)

A deterministic, evidence-based data-quality auditing tool for automatic weather station (AWS) networks. The checker processes multi-station meteorological time series, companion GPS NMEA logs, and volunteer maintenance notes to generate an actionable quality-flag manifest (`flags.csv`) and an executive HTML report (`report.html`).

The project emphasizes more than anomaly detection: it distinguishes likely corrupted observations from unusual but potentially valid weather, preserves conflicting records for investigation, and documents uncertainty before recommending corrective action.

## Architecture

```text
weather-checker/
â”œâ”€â”€ checker.py
â”œâ”€â”€ src/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ ingestion.py
â”‚   â”œâ”€â”€ validation.py
â”‚   â”œâ”€â”€ anomalies.py
â”‚   â”œâ”€â”€ gps.py
â”‚   â”œâ”€â”€ flag_builder.py
â”‚   â””â”€â”€ reporting.py
â”œâ”€â”€ tests/
â”‚   â””â”€â”€ test_checker.py
â”œâ”€â”€ ai_logs/
â”œâ”€â”€ DECISIONS.md
â”œâ”€â”€ flags.csv
â”œâ”€â”€ report.html
â”œâ”€â”€ requirements.txt
â””â”€â”€ README.md
```

| Component | Responsibility |
|---|---|
| `checker.py` | CLI entry point and pipeline coordination |
| `src/ingestion.py` | Input discovery, schema validation, timestamp parsing, and maintenance-log parsing |
| `src/validation.py` | Structural validation, duplicate detection, chronological ordering, and missing-interval checks |
| `src/anomalies.py` | Physical-range checks, isolated spikes, stuck sensors, and maintenance-associated shifts |
| `src/gps.py` | GPS NMEA parsing, coordinate discrepancies, and logger-clock offsets |
| `src/flag_builder.py` | Centralized flag accumulation, interval consolidation, and redundant-flag handling |
| `src/reporting.py` | Executive HTML report generation |
| `tests/test_checker.py` | Automated tests for edge cases, output constraints, and deterministic behavior |
| `DECISIONS.md` | Engineering rationale, trade-offs, and investigation recommendations |
| `ai_logs/` | Development-session AI logs, if retained in the submitted repository |

## Installation

**Requirements**
- Python 3.9 or later.
- Dependencies listed in `requirements.txt`.
- `pytest` to run the test suite.

Create and activate a virtual environment from the repository root.

**Windows**

```powershell
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

## Running the checker

Run the pipeline from the repository root, providing the path to the input data pack:

```bash
python checker.py /path/to/data-pack
```

Example:

```bash
python checker.py ../dataset
```

If supported by the CLI, an alternate output directory can be selected with:

```bash
python checker.py /path/to/data-pack --output-dir /path/to/output
```

The pipeline generates `flags.csv` and `report.html` in its configured output location. Check `python checker.py --help` for the command-line options available in the current implementation.

## Expected input structure

```text
data-pack/
â”œâ”€â”€ stations.csv
â”œâ”€â”€ data/
â”‚   â”œâ”€â”€ S01.csv
â”‚   â”œâ”€â”€ S02.csv
â”‚   â””â”€â”€ ...
â”œâ”€â”€ gps/
â”‚   â”œâ”€â”€ S01.nmea
â”‚   â””â”€â”€ ...
â””â”€â”€ maintenance_log.md
```

Station identifiers are intended to be discovered dynamically from the input metadata and available station files rather than hardcoded to the supplied station IDs.

- **`stations.csv`** â€” Station metadata, including identifiers, names, coordinates, elevation, installation date, and logging interval.
- **`data/Sxx.csv`** â€” Meteorological observations such as `timestamp_utc`, `temp_c`, `rh_pct`, `pressure_hpa`, `wind_kmh`, `wind_dir_deg`, `rain_mm`, and `ghi_wm2`.
- **`gps/Sxx.nmea`** â€” NMEA 0183 GPS sentences, including GPRMC and GPGGA where available.
- **`maintenance_log.md`** â€” Volunteer maintenance records used as contextual evidence when investigating potential hardware-related changes.

GPS logs and maintenance notes may be absent where permitted by the input contract. Missing optional evidence should not automatically be interpreted as proof that a station is healthy.

## Output: `flags.csv`

The flag manifest uses seven columns:

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
| `isolated_spike` | An isolated sensor jump meeting configured detection criteria |
| `stuck_sensor` | Repeated identical readings over the configured minimum duration |
| `abrupt_level_shift` | Persistent change potentially associated with maintenance |
| `post_maintenance_change` | Sustained distribution change following a hardware intervention |
| `clock_offset` | Logger-time discrepancy relative to GPS UTC |
| `gps_location_mismatch` | GPS location inconsistent with installation metadata |
| `gps_invalid_sentence` | Malformed NMEA data or invalid GPS-fix evidence |

The taxonomy describes the intended issue categories. The actual set of emitted categories depends on the input data and the conditions implemented in the current pipeline.

### Action semantics

- **`exclude`** â€” Observations are sufficiently supported as invalid or corrupted that they should not be used without remediation.
- **`correct`** â€” A potential systematic error has an identifiable correction. The correction should be verified against suitable reference data before corrected values are treated as authoritative.
- **`keep`** â€” Retain observations while documenting uncertainty or a potential issue. Unusual measurements should not be discarded solely because they represent extreme weather.

A quality flag records a finding and recommended action. It does not, by itself, mean that the underlying observations have been modified or that a proposed correction has been independently validated.

### Duplicate-record handling

Identical duplicate records and conflicting records with the same timestamp require different treatment. Identical duplicates may be redundant retransmissions, whereas conflicting duplicates contain competing observations that need investigation.

The validation logic is designed to retain conflicting records rather than silently choosing one observation and discarding the other. Any downstream resolution should consider record contents, provenance, and available operational evidence.

## Automated testing

Run the test suite from the repository root:

```bash
python -m pytest tests/test_checker.py -v
```

The latest recorded test run completed successfully with 17 passing tests in 0.95 seconds, using Python 3.13.5 and pytest 9.1.1 on Windows.

The test coverage includes cases such as:
- Extreme but valid temperatures.
- Impossible humidity values.
- Duplicate timestamps and missing intervals.
- Isolated spikes and stuck sensors.
- GPS parsing and maintenance-associated changes.
- Empty or malformed input.
- Dynamic station discovery.
- Deterministic output and flag-schema compliance.

The runtime should also be measured separately from the tests. To obtain a reproducible pipeline runtime, use the same data pack and environment, time a complete run, and report the measured result rather than a previous test duration.

## Determinism and generalization

The checker is designed to produce reproducible outputs for identical inputs.

- **Dynamic discovery:** Station IDs are derived from the supplied input structure where supported.
- **Stable ordering:** Flags are ordered consistently by station, start timestamp, and channel where implemented.
- **Repeatability:** Identical inputs should produce identical outputs, subject to serialization and environment behavior.
- **Local processing:** Normal processing uses the supplied data pack and does not require live weather observations from a network service.

Testing on additional station IDs, altered input conditions, and unseen failure patterns would provide further evidence of generalization beyond the supplied dataset.

## Key investigation areas

The supplied network illustrates several cases where context matters as much as numerical thresholds:

- **Pressure-board replacement:** A persistent pressure shift may indicate a sensor offset, but a reference barometer is needed before a correction can be confirmed.
- **Sensor failures:** Impossible readings and prolonged flatlines can indicate hardware faults rather than genuine atmospheric conditions.
- **Rainfall anomaly:** Rain-gauge observations should be interpreted alongside humidity, solar radiation, and maintenance evidence.
- **GPS discrepancy:** A mismatch between installation metadata and GPS coordinates requires verification of the actual station location.
- **Logger-clock discrepancy:** GPS timing evidence can reveal a possible clock offset. The logger configuration, affected time range, and duplicate records should be investigated before applying timestamp corrections.
- **Anemometer replacement:** A sustained change in measured wind speed after replacement may indicate a calibration difference; it should not automatically be interpreted as a change in weather.
- **Missing and retransmitted observations:** Data gaps and duplicate retransmissions represent distinct data-quality problems and need different handling.

These are investigation hypotheses and operational recommendations, not claims that every suspected cause has been conclusively established. See `DECISIONS.md` for the supporting evidence and trade-offs.

## Known limitations

- An installation-sheet/GPS mismatch requires physical verification before the true station location can be confirmed.
- A pressure-board replacement may introduce a systematic offset, but any proposed correction should be checked against a reference barometer.
- A replacement anemometer with an unknown calibration curve may require the pre- and post-maintenance periods to be treated as separate calibration regimes.
- GPS-derived clock corrections require verification of offset direction, affected time range, and duplicate-record handling before timestamps are changed.
- Automated thresholds may generate false positives, especially for valid extreme weather or legitimate regional shifts.
- Quality flags support engineering decisions; ambiguous cases still require documented human review.

## Dataset licence and attribution

The assignment identifies the weather values as being based on ERA5 reanalysis from the **Copernicus Climate Change Service**, accessed through the **Open-Meteo archive API**, under **Creative Commons Attribution 4.0 International (CC BY 4.0)**.

Station names, people, hardware, and maintenance notes are described as fictional in the assignment data pack. Refer to the original assignment materials for the authoritative dataset provenance and licence terms.
