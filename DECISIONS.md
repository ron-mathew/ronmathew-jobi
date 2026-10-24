# Engineering Decisions and Findings

## 01. The Visit

I recommend dispatching the technician to **S06 (South Hills Fire Station)**.

### The Evidence
On 2025-03-24, a volunteer swapped the battery after a low-voltage period ("Battery was flat... logger rebooted and the clock showed the right time on the screen so I left it"). Following this reboot, the logger clock desynchronized from true UTC. Analyzing the companion GPS file (`gps/S06.nmea`) reveals **439 GPRMC sentences** where the logger timestamp is systematically **6.00 hours behind GPS satellite time** (e.g., logger records 10:10 UTC when GPS reports 16:10 UTC). In addition, the reboot created **36 conflicting duplicate timestamps** between 10:10 and 16:00 on March 24 where morning and afternoon records overlap. Because this affects every channel across the remaining 2.5 weeks of data (distorting the diurnal temperature cycle, peak irradiance, and wind timing), S06 represents the most severe systemic impairment in the network.

### First Inspection Step
The technician should connect a service laptop directly to the data logger's console port, query the internal real-time clock (RTC) against reference UTC, inspect the timezone/offset configuration in firmware (verifying whether it defaulted to local Mountain Standard Time, UTC-6), and extract the uncorrupted internal flash memory ring buffer.

### Confirm vs. Refute
- **Confirmed**: The logger RTC reads 6 hours behind UTC (or UTC-6 MST), and the internal ring buffer contains timestamps aligning with the GPS PPS pulse.
- **Refuted**: The logger RTC is in exact synchronization with GPS UTC, implying the offset occurred in the cellular gateway ingestion pipeline rather than on the station clock itself.

### Why S06 Over Second-Best (S07 and S02)
- **S07 (Anemometer swap)**: On 2025-03-26, an uncalibrated replacement anemometer was fitted, causing recorded wind speeds to plummet by 78% (median dropped from 8.6 km/h to 1.9 km/h). However, a 1-hour field visit without a wind tunnel or calibrated reference cannot restore the historical wind record.
- **S02 (Pressure board swap)**: On 2025-03-21, a replacement board introduced a +10.4 hPa step change. Because atmospheric pressure is regionally coherent, S02 can be mathematically adjusted in post-processing using spatial regression against neighboring stations (S03, S04, S05).
- **Recoverability**: For S06, physical verification of the 6-hour clock offset enables us to mathematically shift the post-March 24 timestamps by +6 hours, recovering 2.5 weeks of high-resolution, multi-channel observations.

---

## 02. The Least-Certain Call

The flag with the weakest evidence is **S05 GPS Location Mismatch** (`channel="metadata"`, `issue="gps_location_mismatch"`, `action="keep"`).

### Context and Evidence
- **Supporting**: The install sheet records S05 at 39.5700°N, -104.8957°E (elevation 1820 m). However, all valid GPS fixes in `gps/S05.nmea` report a mean latitude of **39.7540°N** and longitude of **-104.8957°E**—a discrepancy of exactly **0.1840° (~20.4 km north)**.
- **Opposing**: The longitude matches the install sheet to four decimal places (-104.8957°). More tellingly, 39.7540°N is identical to the installation latitude of station S04. This strongly suggests a clerical copy-paste error on the install sheet rather than a physical displacement or GPS hardware fault. Furthermore, S05's observed pressure (807–849 hPa) and temperature regimes closely match a ~1800 m foothill site.
- **Decision**: Retain data (`action="keep"`) with a clear caveat. The data itself is valid, but the station elevation cannot be independently validated from satellite altitude until location is resolved.
- **Single Information Need**: A physical inspection photo or handheld GPS reading taken at S05's physical location.

---

## 03. Where AI Was Wrong

During development, AI assistance was valuable for rapid prototyping but generated two critical, domain-invalid errors that required manual detection and correction.

### Example 1: Indiscriminate Spike Detection on Wind Gusts
- **AI Suggestion**: *"Apply single-point modified robust z-score spike detection (MRZ > 5.0) across all numerical channels, including wind_kmh, and flag exceeding values as isolated_spike with action='exclude'."*
- **Why It Was Wrong**: Meteorological wind speed is not a symmetric Gaussian variable; it follows a skewed Weibull distribution where isolated wind gusts are physically real. Applying this rule produced **46 false-positive flags** across all eight stations, marking legitimate mountain gusts of 35–60 km/h for deletion.
- **How Detected**: I inspected `flags.csv` by channel and noticed that over 60% of all flags in the network were wind spikes during known regional frontal passages. Cross-checking station barograms confirmed passing cold fronts. I restricted single-point spike detection strictly to channels where single-interval spikes represent physical sensor open circuits or resets (`temp_c`, `pressure_hpa`).

### Example 2: Misclassifying Regional Weather as a Sensor Replacement
- **AI Suggestion**: In `check_abrupt_level_shift`, the AI suggested matching maintenance notes using:
  `any(kw in note.lower() for kw in ("pressure", "board", "sensor", "swap", "reboot", "battery"))`
- **Why It Was Wrong**: On S06, a battery was swapped on 2025-03-24 ("Battery was flat... Swapped battery, logger rebooted"). Because S06's median pressure rose by +3.9 hPa after March 24, the script flagged S06 as an "uncalibrated pressure board replacement." In reality, S06 never had a pressure board replaced. Comparing barometric series across the network showed that S04 (+3.6 hPa), S05 (+4.6 hPa), and S07 (+4.6 hPa) all experienced the exact same +4 hPa synoptic high-pressure ridge over that period.
- **How Detected**: Multi-station cross-comparison revealed that the +4 hPa shift occurred network-wide as natural weather. I restricted keyword matching strictly to `"pressure"` and `"barometer"`.

---

## 04. Next Year: 500 Stations

To scale from 8 stations to 500 stations operating unattended, the architecture would evolve as follows:

1. **Configuration-Driven Rules**: Replace hardcoded bounds with a schema-validated YAML configuration defining physical limits by station environmental class (e.g., alpine ridge, urban basin, agricultural valley).
2. **Dynamic Spatial Baselines**: Replace static 6-week medians with rolling 7-day diurnal medians and spatial consensus (comparing each station's reading against its $k$-nearest neighbors adjusted for elevation via the standard barometric lapse rate).
3. **Incremental Streaming Architecture**: Ingest 10-minute records through an event broker (e.g., Apache Kafka) into an append-only time-series store (such as TimescaleDB or ClickHouse). A lightweight daemon validates incoming records in micro-batches under 50 ms.
4. **Alert Grouping & Suppression**: When a logger experiences power failure or clock drift, group all resultant sensor outages under a single root-cause station alert rather than emitting hundreds of independent channel flags.
5. **Human-in-the-Loop Triage**: Flags with `action="keep"` or `action="correct"` above an impact threshold are routed to an operations triage queue for technician review.
6. **When Distributed Processing Is Needed**: 500 stations $\times$ 144 readings/day $\times$ 8 channels $\approx$ 576,000 observations/day (~100 MB/month uncompressed). A single modern multi-core Python process using Polars or DuckDB processes this monthly volume in under 5 seconds. Distributed processing (e.g. Apache Spark or Ray) is completely unnecessary at 500 stations; it would only become justifiable if ingesting high-frequency 10 Hz raw ultrasonic sonic anemometer streams or scaling to $>50,000$ stations.

---

## 05. By Hand: Solar Noon at S04 on 20 March 2025

### Station Coordinates & Date
- **Station S04 (Foothills Community Garden)**: Latitude $\phi = 39.754^\circ\text{N}$, Longitude $\lambda = -105.146^\circ\text{E}$ (i.e., $105.146^\circ\text{W}$).
- **Date**: 20 March 2025.
- **Day of Year ($N$)**: Non-leap year. January (31) + February (28) + March (20) = **$N = 79$**.

### 1. Fractional Year ($\gamma$)
$$\gamma = \frac{2\pi}{365} (N - 1) = \frac{2\pi}{365} (78) \approx 1.34274 \text{ radians } (\approx 76.933^\circ)$$

### 2. Equation of Time ($EoT$)
Using the standard NOAA / Spencer (1971) formulation (in minutes):
$$EoT = 229.18 \times [0.000075 + 0.001868 \cos\gamma - 0.032077 \sin\gamma - 0.014615 \cos(2\gamma) - 0.040849 \sin(2\gamma)]$$
Evaluating trigonometric terms:
- $\cos(76.933^\circ) \approx 0.22608$
- $\sin(76.933^\circ) \approx 0.97411$
- $\cos(153.866^\circ) \approx -0.89779$
- $\sin(153.866^\circ) \approx 0.44042$

$$EoT = 229.18 \times [0.000075 + 0.000422 - 0.031247 + 0.013121 - 0.017991] = 229.18 \times (-0.035620) \approx -8.163 \text{ minutes}$$
The Equation of Time is **$-8\text{ min } 10\text{ s}$** (apparent solar time lags mean solar time).

### 3. Longitude Correction
Earth rotates $360^\circ$ in 24 hours ($15^\circ$ per hour, or $4\text{ minutes per degree}$).
For $\lambda = 105.146^\circ\text{W}$:
$$\text{Longitude Offset} = 4 \times 105.146 = 420.584 \text{ minutes} = 7 \text{ hours, } 0 \text{ minutes, } 35 \text{ seconds}$$

### 4. Local Solar Noon in UTC
$$\text{Solar Noon (UTC)} = 12:00:00 - \left(\frac{EoT}{60}\right) + \left(\frac{105.146^\circ}{15^\circ/\text{hr}}\right)$$
$$\text{Solar Noon (UTC)} = 12:00:00 - (-00:08:10) + 07:00:35 = \mathbf{19:08:45 \text{ UTC}} \quad (\approx \mathbf{19:09 \text{ UTC}})$$
In Local Standard Time (MST = UTC - 7):
$$\text{Solar Noon (MST)} = 19:08:45 - 07:00:00 = \mathbf{12:08:45 \text{ MST}}$$

### 5. Empirical Verification Against Observed GHI
Inspecting `data/S04.csv` on 2025-03-20 around 19:00 UTC shows:
- 18:50 UTC: $614 \text{ W/m}^2$
- 19:00 UTC: $612 \text{ W/m}^2$
- 19:10 UTC: $730 \text{ W/m}^2$
- 19:20 UTC: $755 \text{ W/m}^2$
- 19:40 UTC: $830 \text{ W/m}^2$ (daily maximum)
- 20:00 UTC: $764 \text{ W/m}^2$

The observed irradiance peaks between 19:10 and 19:40 UTC, consistent with the calculated solar noon of 19:09 UTC and transient afternoon mountain cloud cover.
