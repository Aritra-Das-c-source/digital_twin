"""
Dark Zone Tracking Engine — Runner
========================================
THIS is the script you actually run.

Usage:
    python3 run_pipeline.py <stations.csv> <station_events.csv> <units.csv> [manual_checks.csv]

manual_checks.csv is optional — if given, its VISUAL_ALIGNMENT results are
ingested as Layer 5 (ANDON_SCAN) evidence. Without it, the pipeline still
runs fine on Layers 1+2 alone (pure prediction, no mid/end-cycle correction).
"""

from __future__ import annotations

import sys
import pandas as pd

from dark_zone_tracker import fit_dwell_distribution
from persistence import SQLitePersistence
from orchestrator import DarkZoneOrchestrator
from csv_adapter import inspect_event_types, derive_historical_dwell_csv, load_all_dark_zone_events
from station_config import dark_zone_station_ids


def run(
    stations_csv: str,
    station_events_csv: str,
    units_csv: str,
    manual_checks_csv: str = None,
    db_path: str = "dark_zone_state.db",
):
    # ---- Step 0: figure out which stations are actually dark zones ----
    dz_ids = dark_zone_station_ids(stations_csv)
    print(f"Dark-zone stations (sensor_coverage=NONE): {sorted(dz_ids)}\n")

    print("Distinct event_type values in your CSV:")
    inspect_event_types(station_events_csv)
    print()

    # ---- Step 1 (Layer 1): fit dwell-time distributions, dark zones only ----
    hist_df = derive_historical_dwell_csv(
        station_events_csv, units_csv, dark_zone_station_ids=dz_ids,
    )
    dwell_models = fit_dwell_distribution(hist_df, dist_name="gamma")

    # Station-level fallback for any (station, variant) combo under threshold
    for station in dz_ids:
        fallback_key = (station, "__ALL__")
        if fallback_key not in dwell_models:
            candidates = [v for k, v in dwell_models.items() if k[0] == station]
            if candidates:
                dwell_models[fallback_key] = candidates[0]

    # Last-resort global fallback across all dark-zone stations combined
    if ("__GLOBAL__", "__ALL__") not in dwell_models and len(hist_df) > 0:
        global_hist = hist_df.copy()
        global_hist["station_id"] = "__GLOBAL__"
        global_hist["variant"] = "__ALL__"
        global_fit = fit_dwell_distribution(
            global_hist, dist_name="gamma", min_samples_for_own_fit=1,
        )
        if ("__GLOBAL__", "__ALL__") in global_fit:
            dwell_models[("__GLOBAL__", "__ALL__")] = global_fit[("__GLOBAL__", "__ALL__")]

    print(f"Fitted dwell models for {len({k[0] for k in dwell_models if k[0] != '__GLOBAL__'})} "
          f"dark-zone station(s), {len(dwell_models)} total (station, variant) entries.\n")

    # ---- Step 2: set up persistence + orchestrator ----
    persistence = SQLitePersistence(db_path)
    orch = DarkZoneOrchestrator(
        dwell_models,
        persistence=persistence,
        persist_mode="batched",
        batch_size=100,
        flush_interval_s=2.0,
    )
    print(f"Recovered {len(orch.active)} in-flight vehicle(s) from previous run.\n")

    # ---- Step 3: load combined event stream (entry/exit + Layer 5 Andon), dark zones only ----
    events = load_all_dark_zone_events(
        station_events_csv, units_csv, manual_checks_csv, dark_zone_station_ids=dz_ids,
    )
    print(f"Loaded {len(events)} dark-zone events. Replaying...\n")

    for i, ev in enumerate(events):
        orch.route_event(ev)
        if i % 500 == 0 and i > 0:
            orch.flush()
            print(f"  ...{i}/{len(events)} events processed "
                  f"({len(orch.active)} vehicles currently in-flight)")

    n_flushed = orch.flush()
    print(f"\nFinal flush: {n_flushed} vehicle state(s) written.")

    # ---- Step 4: summary ----
    print(f"\nDone. {len(orch.active)} vehicle(s) still in-flight at end of file.")
    print(f"{len(orch.rejected_log)} event(s) rejected/gated (see orch.rejected_log).")

    no_dwell_model = [r for r in orch.rejected_log if r["reason"] == "no_dwell_model_available"]
    if no_dwell_model:
        print(f"⚠ {len(no_dwell_model)} vehicle(s) could not be tracked — "
              f"no dwell model even at global fallback. Check historical data volume.")

    andon_fails = [e for e in events if e.event_type.value == "andon_scan"
                   and e.payload.get("result") == "FAIL"]
    if andon_fails:
        print(f"({len(andon_fails)} VISUAL_ALIGNMENT FAIL result(s) seen — carried through in "
              f"event.payload, not currently used as filter evidence, available for QA reporting.)")

    return orch


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        print("Usage: python3 run_pipeline.py stations.csv station_events.csv units.csv [manual_checks.csv]")
        sys.exit(1)

    manual_checks_arg = sys.argv[4] if len(sys.argv) == 5 else None
    run(sys.argv[1], sys.argv[2], sys.argv[3], manual_checks_arg)
