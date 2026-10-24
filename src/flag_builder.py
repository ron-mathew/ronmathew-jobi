"""
flag_builder.py – central accumulator for quality flags.

Provides a type-safe, de-duplication-aware store for Flag rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

VALID_ACTIONS = {"exclude", "correct", "keep"}

OUTPUT_COLUMNS = [
    "station_id",
    "channel",
    "start_utc",
    "end_utc",
    "issue",
    "action",
    "note",
]

TS_OUT_FMT = "%Y-%m-%d %H:%M"


@dataclass
class Flag:
    station_id: str
    channel: str
    start_utc: pd.Timestamp
    end_utc: pd.Timestamp
    issue: str
    action: str
    note: str


class FlagBuilder:
    """Accumulates Flag objects and exports them to a canonical DataFrame."""

    def __init__(self) -> None:
        self._flags: List[Flag] = []

    def add(
        self,
        station_id: str,
        channel: str,
        start_utc,
        end_utc,
        issue: str,
        action: str,
        note: str,
    ) -> None:
        if action not in VALID_ACTIONS:
            raise ValueError(
                f"Invalid action '{action}'. Must be one of {VALID_ACTIONS}."
            )
        if not note or not note.strip():
            raise ValueError("Flag note must not be empty.")

        flag = Flag(
            station_id=str(station_id),
            channel=str(channel),
            start_utc=pd.Timestamp(start_utc) if pd.notna(start_utc) else pd.NaT,
            end_utc=pd.Timestamp(end_utc) if pd.notna(end_utc) else pd.NaT,
            issue=str(issue),
            action=str(action),
            note=str(note).strip(),
        )
        self._flags.append(flag)

    def to_dataframe(self) -> pd.DataFrame:
        """Return a deduplicated, sorted DataFrame with exactly the required columns."""
        if not self._flags:
            return pd.DataFrame(columns=OUTPUT_COLUMNS)

        rows = []
        for f in self._flags:
            rows.append(
                {
                    "station_id": f.station_id,
                    "channel": f.channel,
                    "start_utc": (
                        f.start_utc.strftime(TS_OUT_FMT)
                        if pd.notna(f.start_utc)
                        else ""
                    ),
                    "end_utc": (
                        f.end_utc.strftime(TS_OUT_FMT)
                        if pd.notna(f.end_utc)
                        else ""
                    ),
                    "issue": f.issue,
                    "action": f.action,
                    "note": f.note,
                }
            )

        df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)

        # Drop exact duplicates (same station+channel+start+end+issue+action)
        df = df.drop_duplicates(
            subset=["station_id", "channel", "start_utc", "end_utc", "issue", "action"]
        )

        # Prune redundant nested exclude intervals on the same station and channel
        keep_indices = []
        for i, row in df.iterrows():
            if row["action"] != "exclude" or not row["start_utc"] or not row["end_utc"]:
                keep_indices.append(i)
                continue
            r_start = pd.Timestamp(row["start_utc"])
            r_end = pd.Timestamp(row["end_utc"])
            subsumed = False
            for j, other in df.iterrows():
                if i == j:
                    continue
                if (
                    other["station_id"] == row["station_id"]
                    and other["channel"] == row["channel"]
                    and other["action"] == "exclude"
                    and other["start_utc"]
                    and other["end_utc"]
                ):
                    o_start = pd.Timestamp(other["start_utc"])
                    o_end = pd.Timestamp(other["end_utc"])
                    # If other encloses row, row is redundant
                    if (o_start <= r_start and o_end >= r_end) and (o_start < r_start or o_end > r_end or j < i):
                        if o_start == r_start and o_end == r_end and j < i:
                            subsumed = True
                            break
                        elif o_start < r_start or o_end > r_end:
                            subsumed = True
                            break
            if not subsumed:
                keep_indices.append(i)

        df = df.loc[keep_indices].reset_index(drop=True)

        # Stable sort: station_id, start_utc, channel
        df = df.sort_values(
            ["station_id", "start_utc", "channel"], na_position="last"
        ).reset_index(drop=True)

        return df

    def write_csv(self, path) -> None:
        df = self.to_dataframe()
        df.to_csv(path, index=False)
        print(f"Wrote {len(df)} flags to {path}")
