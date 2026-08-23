"""
Dark Zone Tracking Engine — CSV Schema Adapter
====================================================
Maps your actual simulator output into DarkZoneEvent objects:

    station_events.csv : unit_id, station_id, timestamp_ms, event_type
    units.csv           : unit_id, vehicle_model

Two things you MUST configure before this works correctly on real data:

  1. EVENT_TYPE_MAP — maps your raw `event_type` strings to our EventType
     enum. Run `inspect_event_types()` on your CSV first to see the actual
     distinct values, then fill this in.

  2. CHECKPOINT_PROGRESS_MAP — maps (station_id, event_type) -> nominal
     progress fraction (0-1). This is NOT in your CSV and can't be inferred
     — it's "where physically is this checkpoint in the station's work
     sequence," which only you know from the station layout. Placeholder
     values are provided; replace with real ones.
"""

from __future__ import annotations

import pandas as pd
from typing import Optional

from orchestrator import DarkZoneEvent, EventType


# =====================================================================
# CONFIG — edit these two maps for your real station layout
# =====================================================================

# Raw event_type string (from your CSV) -> our EventType enum.
# INSPECT YOUR CSV FIRST (see inspect_event_types below) and adjust keys
# to match exactly what's in the file — these are placeholder guesses.
# Raw event_type string (from your CSV) -> our EventType enum.
# Set a value to None for event types you recognize but deliberately don't
# use (e.g. a queue-arrival event that isn't a dwell-time boundary) — these
# are skipped silently. Omitting a key entirely means "unrecognized," which
# triggers a loud ⚠ warning instead, since that's usually a real mismatch.
EVENT_TYPE_MAP: dict[str, Optional[EventType]] = {
    "UNIT_ARRIVED": None,                          # queue arrival, not a dwell boundary — intentional skip
    "PROCESSING_STARTED": EventType.STATION_ENTRY,  # work begins = dwell clock starts
    "PROCESSING_COMPLETED": EventType.STATION_EXIT, # work ends = dwell clock stops
    # If/when your simulator adds real boundary sensors, extend here, e.g.:
    # "RFID_CHECKPOINT": EventType.RFID_CHECKPOINT,
    # "TORQUE_SPIKE": EventType.POWER_DRAW,
    # "ANDON_SCAN": EventType.ANDON_SCAN,
}

# (station_id, raw_event_type) -> nominal progress fraction.
# EMPTY for now: your current schema has no RFID/power-draw/Andon events, so
# there's nothing to map yet. The filter runs on Layers 1+2 only (predict
# between PROCESSING_STARTED and PROCESSING_COMPLETED, no mid-station
# corrections) — this is a legitimate mode, just without Layer 3-5 benefits.
# Add entries here once your simulator produces real checkpoint events, e.g.:
# ("__DEFAULT__", "RFID_CHECKPOINT"): 0.5,
CHECKPOINT_PROGRESS_MAP: dict[tuple[str, str], float] = {}


# =====================================================================
# INSPECTION HELPER — run this FIRST on your real file
# =====================================================================

def inspect_event_types(station_events_csv: str) -> pd.DataFrame:
    """
    Prints the distinct event_type values in your CSV with counts, so you
    can correctly fill in EVENT_TYPE_MAP and CHECKPOINT_PROGRESS_MAP
    instead of guessing. Run this before your first real replay.

    Distinguishes:
      - genuinely unrecognized values (not in EVENT_TYPE_MAP at all) -> loud warning
      - recognized-but-intentionally-skipped values (mapped to None) -> quiet note
    """
    df = pd.read_csv(station_events_csv)
    counts = df["event_type"].value_counts().reset_index()
    counts.columns = ["event_type", "count"]
    print(counts.to_string(index=False))

    seen = set(df["event_type"].unique())
    unrecognized = seen - set(EVENT_TYPE_MAP.keys())
    intentionally_skipped = {t for t in seen if EVENT_TYPE_MAP.get(t) is None} - unrecognized

    if intentionally_skipped:
        print(f"\n(Intentionally skipped, not an error: {intentionally_skipped})")
    if unrecognized:
        print(f"\n⚠ UNRECOGNIZED event_type values (add these to EVENT_TYPE_MAP): {unrecognized}")
    return counts


def derive_historical_dwell_csv(
    station_events_csv: str,
    units_csv: str,
    output_csv: str = "historical_dwell.csv",
    entry_event_type: str = "PROCESSING_STARTED",
    exit_event_type: str = "PROCESSING_COMPLETED",
) -> pd.DataFrame:
    """
    Builds the historical_dwell.csv that Layer 1 needs (station_id, variant,
    entry_ts, exit_ts) DIRECTLY from your existing station_events.csv +
    units.csv — no separate data source required.

    Pairs each vehicle's `entry_event_type` and `exit_event_type` rows per
    station. Vehicles missing either half of the pair (e.g. still mid-station
    in a truncated simulation run) are dropped with a warning, since a Gamma
    fit needs COMPLETE dwell times, not in-progress ones.

    NOTE: if you're using this to fit the SAME data you're about to replay
    through the filter, that's fine for a first pass/demo, but it's
    circular for real validation — the filter will "predict" data it was
    trained on. For a genuine backtest, fit on an earlier historical batch
    and replay a later, separate batch.
    """
    events_df = pd.read_csv(station_events_csv)
    units_df = pd.read_csv(units_csv)
    variant_lookup = dict(zip(units_df["unit_id"], units_df["vehicle_model"]))

    entries = events_df[events_df["event_type"] == entry_event_type]
    exits = events_df[events_df["event_type"] == exit_event_type]

    merged = entries.merge(
        exits, on=["unit_id", "station_id"], suffixes=("_entry", "_exit"),
    )

    dropped = len(entries) - len(merged)
    if dropped > 0:
        print(f"⚠ Dropped {dropped} unpaired entry event(s) — vehicle likely "
              f"still in-station or missing its exit event in this file.")

    out = pd.DataFrame({
        "station_id": merged["station_id"],
        "variant": merged["unit_id"].map(variant_lookup),
        "entry_ts": pd.to_datetime(merged["timestamp_ms_entry"], unit="ms"),
        "exit_ts": pd.to_datetime(merged["timestamp_ms_exit"], unit="ms"),
    })

    missing_variant = out["variant"].isna().sum()
    if missing_variant > 0:
        print(f"⚠ {missing_variant} row(s) have no variant match in units.csv — "
              f"these will be excluded from station+variant-specific fits.")
    out = out.dropna(subset=["variant"])

    out.to_csv(output_csv, index=False)
    print(f"Wrote {len(out)} historical dwell rows to {output_csv}")
    return out


# =====================================================================
# ADAPTER — CSV rows -> DarkZoneEvent stream
# =====================================================================

def load_events_from_csv(
    station_events_csv: str,
    units_csv: str,
) -> list[DarkZoneEvent]:
    """
    Reads both CSVs, joins unit_id -> vehicle_model (as `variant`), and
    returns a chronologically sorted list of DarkZoneEvent ready to feed
    into DarkZoneOrchestrator.route_event().
    """
    events_df = pd.read_csv(station_events_csv)
    units_df = pd.read_csv(units_csv)

    missing = set(events_df["unit_id"]) - set(units_df["unit_id"])
    if missing:
        print(f"⚠ {len(missing)} unit_id(s) in station_events.csv have no "
              f"matching row in units.csv (variant will be None): "
              f"{list(missing)[:5]}{'...' if len(missing) > 5 else ''}")

    variant_lookup = dict(zip(units_df["unit_id"], units_df["vehicle_model"]))

    events_df = events_df.sort_values("timestamp_ms").reset_index(drop=True)

    events: list[DarkZoneEvent] = []
    unrecognized_types_seen: set[str] = set()
    intentionally_skipped_count = 0

    for row in events_df.itertuples(index=False):
        raw_type = row.event_type

        if raw_type not in EVENT_TYPE_MAP:
            unrecognized_types_seen.add(raw_type)
            continue  # genuinely unknown — skip and warn

        mapped_type = EVENT_TYPE_MAP[raw_type]
        if mapped_type is None:
            intentionally_skipped_count += 1
            continue  # recognized, deliberately not used (e.g. UNIT_ARRIVED) — skip quietly

        vehicle_id = row.unit_id
        station_id = row.station_id
        ts_s = row.timestamp_ms / 1000.0  # ms -> s, matches DarkZoneEvent.ts contract
        variant = variant_lookup.get(vehicle_id)

        checkpoint_progress: Optional[float] = None
        if mapped_type in (EventType.RFID_CHECKPOINT, EventType.POWER_DRAW, EventType.ANDON_SCAN):
            checkpoint_progress = CHECKPOINT_PROGRESS_MAP.get(
                (station_id, raw_type),
                CHECKPOINT_PROGRESS_MAP.get(("__DEFAULT__", raw_type)),
            )
            if checkpoint_progress is None:
                unrecognized_types_seen.add(f"{raw_type} (no progress mapping for station {station_id})")
                continue

        events.append(DarkZoneEvent(
            event_type=mapped_type,
            vehicle_id=vehicle_id,
            station_id=station_id,
            ts=ts_s,
            variant=variant,
            checkpoint_progress=checkpoint_progress,
        ))

    if intentionally_skipped_count:
        print(f"(Skipped {intentionally_skipped_count} intentionally-unused event(s), e.g. queue arrivals.)")
    if unrecognized_types_seen:
        print(f"⚠ Skipped events with UNRECOGNIZED types/configs: {unrecognized_types_seen}")

    return events
