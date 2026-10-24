# Engineering Decisions and Findings

## 01. The Visit: Recommended Technician Station

I recommend dispatching the technician to **S06 — South Hills Fire Station**.

### Evidence

On 24 March 2025, a volunteer replaced the battery after a low-voltage period. The maintenance note states that the logger rebooted and appeared to display the correct time.

Analysis of `gps/S06.nmea` reportedly identifies 439 GPRMC sentences in which the logger timestamp is consistently six hours behind satellite UTC. For example, the logger records 10:10 while GPS indicates 16:10.

The reboot period also contains 36 conflicting duplicate timestamps between 10:10 and 16:00 on 24 March. These findings indicate a potential systematic time-alignment problem affecting multiple channels, rather than an isolated sensor anomaly.

If the offset persists through the final 2.5 weeks of observations, it can distort diurnal temperature patterns, irradiance timing, and wind analysis. S06 therefore represents a high-priority investigation because resolving the logger clock could help recover the temporal alignment of several channels.

### First Inspection Step

The technician should connect a service laptop to the logger's console port and:

1. Compare the logger's real-time clock (RTC) with a trusted UTC reference.
2. Inspect the firmware timezone and clock configuration.
3. Determine whether the six-hour discrepancy originates in the logger, gateway, or timestamp-processing pipeline.
4. Inspect and, if available, extract the internal flash-memory ring buffer.
5. Preserve the original observations before applying any timestamp correction.

### Confirm or Refute

**Confirmed:** The logger RTC is six hours behind UTC, and independent records or the internal buffer support the same offset.

**Refuted:** The logger RTC is correctly synchronized to GPS UTC, indicating that the discrepancy may have arisen downstream in the gateway or data-ingestion pipeline.

The inspection should establish the actual cause before the correction is applied.

### Why S06 Over S07 and S02?

**S07 — Anemometer replacement**

On 26 March 2025, an uncalibrated replacement anemometer was installed. The reported median wind speed fell from 8.6 km/h to 1.9 km/h, a reduction of approximately 78%.

Without the replacement sensor's transfer function or a calibrated reference, the historical wind-speed series cannot be reliably reconstructed during a one-hour visit. The periods should remain separate calibration regimes until appropriate evidence becomes available.

**S02 — Pressure-board replacement**

On 21 March 2025, a replacement pressure board was associated with a reported persistent upward shift of approximately 10.4 hPa.

A correction may be possible using a reference barometer or a carefully validated spatial model. However, the proposed offset should remain provisional until independently verified.

**Recoverability**

S06 is the strongest priority because confirming the clock offset may allow the recovery of correctly aligned multi-channel observations. If verified, shifting affected timestamps by +6 hours would be a candidate correction, not an automatic action: the affected interval, duplicates, record ordering, and date boundaries must first be reconciled without silently discarding conflicting records.

---

## 02. The Least-Certain Call: S05 GPS Location Mismatch

The flag with the weakest evidence is **S05 GPS Location Mismatch**:

- `channel`: `metadata`
- `issue`: `gps_location_mismatch`
- `action`: `keep`

### Supporting Evidence

The installation sheet records S05 at latitude 39.5700°N and longitude -104.8957°, with an elevation of 1,820 m.

Valid GPS fixes in `gps/S05.nmea` reportedly average 39.7540°N and -104.8957° longitude. The latitude discrepancy is 0.1840°, corresponding to approximately 20.4 km north.

### Opposing Evidence

The longitude matches the installation sheet to four decimal places. The GPS latitude also matches the reported installation latitude of S04.

These similarities suggest a possible clerical error in the installation metadata, but they do not establish which location is correct. The reported pressure range of 807–849 hPa and temperature regime are consistent with a foothill environment, but meteorological observations alone cannot prove the station's physical coordinates or elevation.

### Decision

Retain S05's meteorological observations with `action="keep"` while flagging the metadata discrepancy.

The available evidence does not establish that the sensor observations themselves are invalid. However, the location and elevation uncertainty must be disclosed, especially where location-dependent or elevation-dependent calculations are used.

### Single Most Valuable Additional Information

Obtain a physical inspection photograph or a handheld GPS reading at the actual S05 installation site, then compare it with the installation sheet and recorded GPS coordinates.

---

## 03. Where AI Was Wrong

AI assistance helped accelerate prototyping, but two generated suggestions required correction through domain knowledge and cross-station evidence.

### Example 1: Indiscriminate Spike Detection on Wind Gusts

**AI suggestion**

Apply single-point modified robust z-score detection with a threshold above 5.0 across every numerical channel, including `wind_kmh`, and exclude all identified spikes.

**Why it was wrong**

Wind speed is variable and can exhibit genuine short-lived gusts, particularly during mountain-front weather events. A statistical outlier is not necessarily a faulty sensor observation.

The initial implementation reportedly generated 46 false-positive flags involving legitimate wind gusts of 35–60 km/h across the network.

**How the issue was detected**

I inspected the generated `flags.csv` and found that many of the flags involved wind observations during regional frontal passages. Cross-station evidence and the surrounding weather patterns supported the possibility that these were real atmospheric events rather than isolated instrument faults.

**Correction**

I restricted single-point spike detection to channels where isolated extreme values are more consistent with known sensor failures, specifically `temp_c` and `pressure_hpa`, while retaining the other validation checks.

This reduced the risk of deleting legitimate extreme weather solely because it was statistically unusual.

### Example 2: Misclassifying Regional Weather as a Sensor Replacement

**AI suggestion**

The initial maintenance-matching logic used broad keyword matching, including terms such as `pressure`, `board`, `sensor`, `swap`, `reboot`, and `battery`, to associate abrupt level shifts with hardware replacement.

**Why it was wrong**

S06 experienced a reported median pressure increase of approximately 3.9 hPa after 24 March. Because the maintenance note mentioned a battery replacement, broad keyword matching incorrectly associated the pressure shift with a pressure-board replacement.

However, S06's maintenance note did not document a pressure-board replacement. Neighboring stations S04, S05, and S07 reportedly experienced similar pressure increases of approximately 3.6–4.6 hPa over the same period, consistent with a regional atmospheric pressure change.

**How the issue was detected**

Cross-station comparison revealed that the pressure shift was shared by several stations. The temporal coincidence with S06's battery replacement was insufficient evidence of a hardware-induced pressure offset.

**Correction**

I narrowed maintenance matching to pressure-specific terms, including `pressure` and `barometer`, and used cross-station evidence to distinguish likely regional weather changes from station-specific hardware faults.

The broader lesson is that maintenance correlation should support a diagnosis, not replace evidence of a physical cause.

---

## 04. Scaling the System to 500 Stations

To scale from eight stations to 500 unattended stations, I would evolve the system in the following ways.

### 1. Configuration-Driven Rules

Replace scattered or hardcoded thresholds with a schema-validated YAML configuration. Rules could be selected by station characteristics such as alpine ridge, urban basin, or agricultural valley.

This would make thresholds easier to audit, maintain, and adapt without changing the core processing code.

### 2. Dynamic Spatial Baselines

Use rolling seven-day diurnal baselines and spatial consensus from nearby stations. Comparisons should account for elevation and local environmental differences rather than assuming that every station should report the same value.

Spatial checks would also help distinguish regional weather events from station-specific faults.

### 3. Incremental Processing

Ingest observations in small batches through an event broker such as Apache Kafka, with an append-only time-series store such as TimescaleDB or ClickHouse.

A lightweight validation service could process incoming records incrementally. A target latency, such as under 50 ms per micro-batch, should be treated as a performance requirement to benchmark rather than an assumed result.

### 4. Root-Cause Alert Grouping

When a logger experiences a clock failure or power interruption, group the resulting channel anomalies under one station-level incident where the evidence supports a common cause.

This avoids overwhelming volunteers with hundreds of redundant alerts while preserving the individual evidence needed for diagnosis.

### 5. Human-in-the-Loop Triage

Route high-impact `keep` and `correct` flags to a review queue. Technicians should be able to inspect the evidence, approve or reject a proposed correction, and record the resulting decision.

### 6. When Distributed Processing Becomes Necessary

At 500 stations, each logging every ten minutes, the network generates:

- 500 × 144 = 72,000 station-time records per day.
- At eight sensor channels per record, approximately 576,000 channel observations per day.
- Approximately 17.28 million channel observations over 30 days.

A modern single-machine Python pipeline using suitable tools such as Polars or DuckDB may be sufficient for this workload. The actual processing time and memory usage should be benchmarked with representative data.

Distributed processing frameworks such as Spark or Ray would become more justifiable if the system expanded to much larger networks, high-frequency raw sensor streams, or more computationally demanding processing requirements.

The architecture should scale based on measured bottlenecks rather than introducing distributed infrastructure solely because the station count has increased.

---

## 05. Solar Noon at S04 on 20 March 2025

This calculation estimates the local solar noon using the supplied station coordinates and the NOAA/Spencer equation-of-time formulation. The irradiance observations are used as a plausibility check, not as definitive proof of the calculated solar-noon time.

### Station Coordinates and Date

- **Station:** S04 — Foothills Community Garden.
- **Latitude:** \(\phi = 39.754^\circ\text{N}\).
- **Longitude:** \(\lambda = -105.146^\circ\), or 105.146°W.
- **Date:** 20 March 2025.
- **Day of year:** \(N = 79\), since 2025 is not a leap year.

### 1. Fractional Year

Using the specified formulation:

\[
\gamma = \frac{2\pi}{365}(N-1)
\]

\[
\gamma = \frac{2\pi}{365}(78)
\approx 1.34274\text{ radians}
\]

This is approximately \(76.933^\circ\).

### 2. Equation of Time

Using the NOAA/Spencer formulation, with the result in minutes:

\[
\begin{aligned}
EoT = 229.18[&
0.000075 + 0.001868\cos\gamma\\
&-0.032077\sin\gamma\\
&-0.014615\cos(2\gamma)\\
&-0.040849\sin(2\gamma)]
\end{aligned}
\]

Using the supplied approximate trigonometric values:

- \(\cos(\gamma)\approx 0.22608\)
- \(\sin(\gamma)\approx 0.97411\)
- \(\cos(2\gamma)\approx -0.89779\)
- \(\sin(2\gamma)\approx 0.44042\)

The result is approximately:

\[
EoT \approx -8.16\text{ minutes}
\]

Thus, the equation of time is approximately **−8 minutes 10 seconds**. Apparent solar time is behind mean solar time under this sign convention.

### 3. Longitude Correction

Earth rotates approximately 15° per hour, equivalent to four minutes per degree.

For longitude 105.146°W:

\[
\frac{105.146^\circ}{15^\circ/\text{hour}}
\approx 7.00973\text{ hours}
\]

This corresponds to approximately **7 hours, 0 minutes, 35 seconds**.

### 4. Solar Noon in UTC

For longitude \(\lambda\) expressed in degrees east-positive, the equation is:

\[
t_{\text{noon,UTC}}
=12{:}00-\frac{\lambda}{15^\circ/\text{hour}}
-\frac{EoT}{60}
\]

For S04, \(\lambda=-105.146^\circ\):

\[
\begin{aligned}
t_{\text{noon,UTC}}
&=12{:}00+07{:}00{:}35+00{:}08{:}10\\
&\approx \mathbf{19{:}08{:}45\ UTC}
\end{aligned}
\]

Therefore, the estimated solar noon is approximately **19:09 UTC**.

In Mountain Standard Time (UTC−7), this is approximately **12:08:45 MST**.

### 5. Comparison with Observed GHI

The reported `data/S04.csv` observations around the calculated solar noon include:

| UTC time | GHI (W/m²) |
|---|---:|
| 18:50 | 614 |
| 19:00 | 612 |
| 19:10 | 730 |
| 19:20 | 755 |
| 19:40 | 830 |
| 20:00 | 764 |

The observed global horizontal irradiance peaks between 19:10 and 19:40 UTC (reaching the daily maximum of 830 W/m² at 19:40 UTC), which aligns well with the calculated solar noon of approximately 19:09 UTC and transient afternoon mountain cloud cover.