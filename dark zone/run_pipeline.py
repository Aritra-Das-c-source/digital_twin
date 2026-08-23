"""
Dark Zone Tracking Engine — Runner
========================================
THIS is the script you actually run. Everything else (dark_zone_tracker.py,
persistence.py, orchestrator.py, csv_adapter.py) is a module it imports —
you never call `python3 persistence.py` etc. directly in normal operation.

Usage:
    python3 run_pipeline.py station_events.csv units.csv historical_dwell.csv
"""

from __future__ import annotations

import sys
import pandas as pd

from dark_zone_tracker import fit_dwell_distribution
from persistence import SQLitePersistence
from orchestrator import DarkZoneOrchestrator
from csv_adapter import load_events_from_csv, inspect_event_types, derive_historical_dwell_csv


def run(
    station_events_csv: str,
    units_csv: str,
    historical_dwell_csv: str = None,   # if None, derive it from station_events_csv itself
    db_path: str = "dark_zone_state.db",
):
    # ---- Step 0: sanity-check the raw event types before doing anything else ----
    print("Distinct event_type values in your CSV:")
    inspect_event_types(station_events_csv)
    print()

    # ---- Step 1 (Layer 1): fit dwell-time distributions from historical data ----
    # historical_dwell_csv needs columns: station_id, variant, entry_ts, exit_ts
    # If you don't have this as a separate file, derive it from your existing
    # station_events.csv (paired entry/exit events per vehicle).
    if historical_dwell_csv is None:
        print("No historical_dwell_csv provided — deriving one from station_events.csv...")
        hist_df = derive_historical_dwell_csv(station_events_csv, units_csv)
    else:
        hist_df = pd.read_csv(historical_dwell_csv)
    dwell_models = fit_dwell_distribution(hist_df, dist_name="gamma")

    # Ensure every station has a fallback entry so unseen/rare variants don't
    # crash the orchestrator on spawn — see DarkZoneOrchestrator._spawn.
    stations = hist_df["station_id"].unique()
    for station in stations:
        fallback_key = (station, "__ALL__")
        if fallback_key not in dwell_models:
            # Grab any fit for this station as the fallback
            candidates = [v for k, v in dwell_models.items() if k[0] == station]
            if candidates:
                dwell_models[fallback_key] = candidates[0]

    # Last-resort GLOBAL fallback: fit one distribution across ALL historical
    # dwell samples, ignoring station/variant entirely. This covers stations
    # with too little data for even a station-level fit (common with small
    # test datasets, or a brand-new station with no history yet). Real
    # production data should rarely need this — if you see it firing a lot,
    # that's a signal a specific station needs more historical samples, not
    # that the pipeline is broken.
    global_hist = hist_df.copy()
    global_hist["station_id"] = "__GLOBAL__"
    global_hist["variant"] = "__ALL__"
    global_fit = fit_dwell_distribution(
        global_hist, dist_name="gamma", min_samples_for_own_fit=1,
    )
    if ("__GLOBAL__", "__ALL__") in global_fit:
        dwell_models[("__GLOBAL__", "__ALL__")] = global_fit[("__GLOBAL__", "__ALL__")]

    # ---- Step 2: set up persistence + orchestrator ----
    persistence = SQLitePersistence(db_path)
    orch = DarkZoneOrchestrator(
        dwell_models,
        persistence=persistence,
        persist_mode="batched",     # fast replay; call orch.flush() at checkpoints
        batch_size=100,
        flush_interval_s=2.0,
    )
    print(f"Recovered {len(orch.active)} in-flight vehicle(s) from previous run.\n")

    # ---- Step 3: load and replay events ----
    events = load_events_from_csv(station_events_csv, units_csv)
    print(f"Loaded {len(events)} events. Replaying...\n")

    for i, ev in enumerate(events):
        orch.route_event(ev)

        # Periodic progress + safety flush every 500 events, independent of
        # the orchestrator's own batch/interval trigger — belt and suspenders
        # for a long replay run.
        if i % 500 == 0 and i > 0:
            orch.flush()
            print(f"  ...{i}/{len(events)} events processed "
                  f"({len(orch.active)} vehicles currently in-flight)")

    # ---- Step 4: final flush — don't lose the tail of the run ----
    n_flushed = orch.flush()
    print(f"\nFinal flush: {n_flushed} vehicle state(s) written.")

    # ---- Step 5: summary ----
    print(f"\nDone. {len(orch.active)} vehicle(s) still in-flight at end of file.")
    print(f"{len(orch.rejected_log)} event(s) rejected/gated (see orch.rejected_log).")

    no_dwell_model = [r for r in orch.rejected_log if r["reason"] == "no_dwell_model_available"]
    if no_dwell_model:
        print(f"⚠ {len(no_dwell_model)} vehicle(s) could not be tracked at all — "
              f"no dwell model available even at global fallback level. Check historical data volume.")

    return orch


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print("Usage: python3 run_pipeline.py station_events.csv units.csv [historical_dwell.csv]")
        print("  (if historical_dwell.csv is omitted, it's auto-derived from station_events.csv)")
        sys.exit(1)

    hist_arg = sys.argv[3] if len(sys.argv) == 4 else None
    run(sys.argv[1], sys.argv[2], hist_arg)
