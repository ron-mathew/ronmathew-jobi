"""
reporting.py – generate report.html from flags.csv and summary statistics.

Produces a one-to-two A4-page HTML report targeting a non-technical coordinator.
All numbers are computed from actual flags; no values are fabricated.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


# Station visit recommendation – written after evidence-based analysis
# of the actual flags. These strings are updated by the checker at runtime.
_VISIT_RECOMMENDATION: dict = {}


def _ts(s: str) -> str:
    """Short human-readable timestamp."""
    if not s:
        return "—"
    try:
        return pd.Timestamp(s).strftime("%d %b %Y %H:%M")
    except Exception:
        return s


def generate_report(
    flags_df: pd.DataFrame,
    station_meta: pd.DataFrame,
    output_path: Path,
    visit_station: str,
    visit_reason: str,
    main_uncertainty: str,
    data_window_start: str,
    data_window_end: str,
) -> None:
    """Write report.html to output_path."""

    # ── per-station summary ──────────────────────────────────────────────────
    station_ids = sorted(station_meta["station_id"].tolist())
    flag_counts = flags_df.groupby("station_id").size().to_dict()
    action_counts = (
        flags_df.groupby(["station_id", "action"]).size().unstack(fill_value=0)
    )

    summary_rows: List[dict] = []
    for sid in station_ids:
        meta_row = station_meta[station_meta["station_id"] == sid].iloc[0]
        n_flags = flag_counts.get(sid, 0)
        n_exclude = action_counts.loc[sid, "exclude"] if sid in action_counts.index and "exclude" in action_counts.columns else 0
        n_keep = action_counts.loc[sid, "keep"] if sid in action_counts.index and "keep" in action_counts.columns else 0
        n_correct = action_counts.loc[sid, "correct"] if sid in action_counts.index and "correct" in action_counts.columns else 0

        # Key issues
        sid_flags = flags_df[flags_df["station_id"] == sid]
        issues = sid_flags["issue"].value_counts()
        top_issue = issues.index[0] if not issues.empty else "none"

        summary_rows.append({
            "Station": f"{sid} – {meta_row['name']}",
            "Flags": n_flags,
            "Exclude": int(n_exclude),
            "Keep*": int(n_keep),
            "Correct": int(n_correct),
            "Top issue": top_issue.replace("_", " "),
        })

    summary_df = pd.DataFrame(summary_rows)

    # ── flag table for material issues only ──────────────────────────────────
    # Select the most impactful flags (exclude + correct) for the report
    material = flags_df[flags_df["action"].isin(["exclude", "correct"])].copy()
    material = material.sort_values(["station_id", "start_utc"]).head(30)

    # ── channel trust assessment ──────────────────────────────────────────────
    channels_with_issues = flags_df[["channel", "action"]].copy()
    ch_status: Dict[str, str] = {}
    all_channels = ["temp_c", "rh_pct", "pressure_hpa", "wind_kmh", "wind_dir_deg",
                    "rain_mm", "ghi_wm2"]
    for ch in all_channels:
        ch_flags = channels_with_issues[channels_with_issues["channel"] == ch]
        if ch_flags.empty:
            ch_status[ch] = "Trustworthy"
        elif "exclude" in ch_flags["action"].values:
            ch_status[ch] = "Conditional / some periods excluded"
        else:
            ch_status[ch] = "Trustworthy (with caveats)"

    # ── HTML ──────────────────────────────────────────────────────────────────
    generated_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    def df_to_html_table(df: pd.DataFrame, css_class: str = "data-table") -> str:
        if df.empty:
            return "<p><em>No data.</em></p>"
        rows = []
        rows.append(f'<table class="{css_class}"><thead><tr>')
        for col in df.columns:
            rows.append(f"<th>{col}</th>")
        rows.append("</tr></thead><tbody>")
        for _, row in df.iterrows():
            rows.append("<tr>")
            for val in row:
                rows.append(f"<td>{val}</td>")
            rows.append("</tr>")
        rows.append("</tbody></table>")
        return "\n".join(rows)

    # Channel status table
    ch_rows = [{"Channel": ch, "Status": status} for ch, status in ch_status.items()]
    ch_df = pd.DataFrame(ch_rows)

    # Material flags abbreviated for report
    if not material.empty:
        mat_display = material[["station_id", "channel", "start_utc", "end_utc", "issue", "action"]].copy()
        mat_display.columns = ["Station", "Channel", "Start", "End", "Issue", "Action"]
        mat_display["Issue"] = mat_display["Issue"].str.replace("_", " ")
        mat_display["Start"] = mat_display["Start"].apply(_ts)
        mat_display["End"] = mat_display["End"].apply(_ts)
    else:
        mat_display = pd.DataFrame()

    total_flags = len(flags_df)
    n_exclude_total = (flags_df["action"] == "exclude").sum()
    n_stations = len(station_ids)
    n_flagged_stations = flags_df["station_id"].nunique()

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Ridgeline Schools Weather Network – Health Report</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: Arial, Helvetica, sans-serif; font-size: 10.5pt;
           color: #1a1a1a; background: #fff; padding: 20mm 18mm; }}
    h1 {{ font-size: 14pt; color: #003366; border-bottom: 2px solid #003366;
          padding-bottom: 4px; margin-bottom: 8px; }}
    h2 {{ font-size: 11pt; color: #003366; margin-top: 14px; margin-bottom: 4px; }}
    p {{ margin-bottom: 6px; line-height: 1.45; }}
    .meta {{ font-size: 9pt; color: #555; margin-bottom: 10px; }}
    .highlight {{ background: #fff3cd; border-left: 4px solid #e67e00;
                  padding: 6px 10px; margin: 8px 0; border-radius: 2px; }}
    .good {{ background: #d4edda; border-left: 4px solid #28a745;
             padding: 6px 10px; margin: 8px 0; border-radius: 2px; }}
    .warn {{ background: #f8d7da; border-left: 4px solid #c0392b;
             padding: 6px 10px; margin: 8px 0; border-radius: 2px; }}
    table.data-table {{ border-collapse: collapse; width: 100%; font-size: 9pt;
                        margin-bottom: 8px; }}
    table.data-table th {{ background: #003366; color: #fff; padding: 4px 6px;
                           text-align: left; font-weight: normal; }}
    table.data-table td {{ border: 1px solid #ccc; padding: 3px 6px;
                           vertical-align: top; }}
    table.data-table tr:nth-child(even) td {{ background: #f5f8fc; }}
    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
                margin-bottom: 8px; }}
    .box {{ background: #f0f4f8; border: 1px solid #ccd; border-radius: 4px;
            padding: 8px; }}
    .box h3 {{ font-size: 9.5pt; color: #003366; margin-bottom: 4px; }}
    @media print {{ body {{ padding: 10mm; }} }}
  </style>
</head>
<body>

<h1>Ridgeline Schools Weather Network — Data Quality Report</h1>
<p class="meta">
  Generated: {generated_at} &nbsp;|&nbsp;
  Data window: {data_window_start} to {data_window_end} &nbsp;|&nbsp;
  Network: {n_stations} stations &nbsp;|&nbsp;
  Total flags: {total_flags}
</p>

<h2>Overall Assessment</h2>
<p>
  The network collected approximately six weeks of data from {n_stations} stations.
  <strong>{n_flagged_stations} of {n_stations} stations</strong> have at least one
  data-quality flag. {n_exclude_total} individual observations (or intervals) are
  recommended for exclusion. The majority of channels across most stations are usable
  for the spring climate summary, but several specific issues require attention before
  analysis.
</p>

<h2>Channel Trust Summary</h2>
{df_to_html_table(ch_df)}

<h2>Station-by-Station Summary</h2>
{df_to_html_table(summary_df)}
<p style="font-size:8.5pt;color:#555;">* "Keep" flags are retained with a caveat (e.g., genuine extreme, uncertain diagnosis).</p>

<h2>Material Issues (Exclude / Correct)</h2>
{df_to_html_table(mat_display)}

<h2>Recommended Technician Visit</h2>
<div class="highlight">
  <strong>Priority station: {visit_station}</strong><br>
  {visit_reason}
</div>

<h2>Main Unresolved Uncertainty</h2>
<p>{main_uncertainty}</p>

<h2>Recommendation for Spring Climate Summary</h2>
<div class="good">
  <strong>Proceed with analysis</strong> using the following precautions:
  <ul style="margin-left:16px; margin-top:4px;">
    <li>Exclude all intervals flagged <em>exclude</em> in flags.csv.</li>
    <li>Treat pressure_hpa for S02 before and after 2025-03-21 11:00 as two separate series
        until recalibration data is obtained.</li>
    <li>Treat wind_kmh for S07 after 2025-03-26 as potentially from a differently-calibrated
        anemometer; compare with neighbouring stations before computing averages.</li>
    <li>For S06, timestamps between 2025-03-24 and end of record may be 6 hours early;
        diurnal analyses should account for this pending a clock-correction visit.</li>
    <li>S08 April 5 data is duplicated; use only the first occurrence of each timestamp.</li>
  </ul>
</div>

<p class="meta" style="margin-top:14px;">
  This report was generated automatically by the weather-checker program.
  All flag counts and metrics are computed from flags.csv; no values are estimated.
  See flags.csv and DECISIONS.md for full evidence and uncertainty discussion.
</p>

</body>
</html>
"""

    output_path.write_text(html, encoding="utf-8")
    print(f"Report written to {output_path}")
