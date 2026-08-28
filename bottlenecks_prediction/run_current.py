"""Interactive one-click runner for the final bottleneck prediction pipeline.

Deployment contract
-------------------
1. The simulator writes the completed run into ``data/input/current_run/``.
2. Prior completed runs live under ``data/calibration/history/<run_name>/``.
3. The user selects DARK station IDs and starts this launcher.
4. This launcher builds topology-specific calibration ONLY from prior history,
   configures LIGHT/DARK routing, and runs the exact ``main.py replay`` pipeline.

No calibration is learned from ``current_run`` in this launcher.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CURRENT_RUN = ROOT / "data" / "input" / "current_run"
HISTORY_ROOT = ROOT / "data" / "calibration" / "history"
GENERATED_CALIBRATION = ROOT / "data" / "calibration" / "generated"
CONFIGURED_STATIONS = ROOT / "data" / "input" / "configured_stations.csv"
DEFAULT_OUTPUT = ROOT / "data" / "output" / "predictions.jsonl"
STATION_CHECKPOINTS = ROOT / "config" / "station_checkpoints.csv"
DARK_ZONE_DIR = ROOT / "dark_zone"


def _run(cmd: list[str]) -> None:
    print("\n>", " ".join(cmd))
    completed = subprocess.run(cmd, cwd=ROOT)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {completed.returncode}")


def _required_current_files() -> dict[str, Path]:
    files = {
        "stations.csv": CURRENT_RUN / "stations.csv",
        "units.csv": CURRENT_RUN / "units.csv",
        "station_events.csv": CURRENT_RUN / "station_events.csv",
    }
    missing = [name for name, path in files.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Simulator input is incomplete. Missing from data/input/current_run/: "
            + ", ".join(missing)
        )
    return files


def _validate_input_schema(files: dict[str, Path]) -> pd.DataFrame:
    stations = pd.read_csv(files["stations.csv"])
    units = pd.read_csv(files["units.csv"])
    events = pd.read_csv(files["station_events.csv"], nrows=20)

    station_required = {"station_id"}
    unit_required = {"unit_id", "vehicle_model"}
    event_required = {"timestamp_ms", "station_id", "unit_id", "event_type"}

    for name, frame, required in (
        ("stations.csv", stations, station_required),
        ("units.csv", units, unit_required),
        ("station_events.csv", events, event_required),
    ):
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{name} missing required columns: {sorted(missing)}")

    stations = stations.copy()
    stations["station_id"] = stations["station_id"].astype(str).str.strip()
    if stations["station_id"].duplicated().any():
        dup = stations.loc[stations["station_id"].duplicated(), "station_id"].tolist()
        raise ValueError(f"Duplicate station IDs in stations.csv: {dup}")
    return stations


def _parse_dark_stations(raw: str, available: list[str]) -> list[str]:
    requested = []
    seen = set()
    for token in str(raw).split(","):
        sid = token.strip()
        if not sid or sid in seen:
            continue
        requested.append(sid)
        seen.add(sid)
    invalid = sorted(set(requested) - set(available))
    if invalid:
        raise ValueError(
            "Invalid DARK station(s): "
            + ", ".join(invalid)
            + ". Available: "
            + ", ".join(available)
        )
    return requested


def _history_runs() -> list[Path]:
    if not HISTORY_ROOT.is_dir():
        return []
    runs = []
    for p in sorted(HISTORY_ROOT.iterdir()):
        if not p.is_dir():
            continue
        if all((p / name).is_file() for name in ("stations.csv", "units.csv", "station_events.csv")):
            runs.append(p)
    return runs


def _load_calibration_helpers():
    dark_text = str(DARK_ZONE_DIR)
    if dark_text not in sys.path:
        sys.path.insert(0, dark_text)
    from csv_adapter import derive_historical_dwell_csv  # type: ignore
    from build_corridor_residence_calibration import build_one_run  # type: ignore
    from runtime.runtime_controller import derive_dark_topology

    return derive_historical_dwell_csv, build_one_run, derive_dark_topology


def _build_prior_calibration(dark_stations: list[str]) -> tuple[Path | None, Path | None, dict]:
    if not dark_stations:
        return None, None, {"history_runs": [], "corridors": []}

    runs = _history_runs()
    if not runs:
        raise FileNotFoundError(
            "DARK stations were selected but no prior calibration runs exist under "
            "data/calibration/history/. Add at least one completed historical run "
            "containing stations.csv, units.csv, and station_events.csv."
        )

    derive_dwell, build_corridor_one_run, derive_dark_topology = _load_calibration_helpers()
    GENERATED_CALIBRATION.mkdir(parents=True, exist_ok=True)

    # 1) Historical processing-dwell calibration for the selected DARK stations.
    dwell_parts: list[pd.DataFrame] = []
    for run in runs:
        temp = GENERATED_CALIBRATION / f"_dwell_{run.name}.csv"
        part = derive_dwell(
            str(run / "station_events.csv"),
            str(run / "units.csv"),
            output_csv=str(temp),
            dark_zone_station_ids=set(dark_stations),
        )
        if not part.empty:
            part = part.copy()
            part["source_run"] = run.name
            dwell_parts.append(part)
        temp.unlink(missing_ok=True)

    if not dwell_parts:
        raise ValueError(
            "Historical runs contain no complete dwell intervals for the selected DARK stations."
        )

    dwell = pd.concat(dwell_parts, ignore_index=True)
    dwell_path = GENERATED_CALIBRATION / "historical_dwell.csv"
    dwell.to_csv(dwell_path, index=False)

    # 2) Exact current topology, but calibrated only from PRIOR completed runs.
    configured = pd.read_csv(CONFIGURED_STATIONS)
    _, corridors = derive_dark_topology(configured)
    residence_rows: list[dict] = []
    corridor_summaries = []
    for corridor in corridors.values():
        sequence = list(corridor.sequence)
        before = len(residence_rows)
        used = []
        for run in runs:
            rows = build_corridor_one_run(
                run,
                sequence,
                upstream_station=corridor.upstream_light_station,
            )
            if rows:
                residence_rows.extend(rows)
                used.append(run.name)
        count = len(residence_rows) - before
        corridor_summaries.append(
            {
                "corridor_id": corridor.zone_id,
                "sequence": sequence,
                "upstream_light_station": corridor.upstream_light_station,
                "rows": count,
                "history_runs": used,
            }
        )
        if count == 0:
            raise ValueError(
                "No causal historical corridor-residence calibration could be built for "
                f"{sequence}. Add an older completed run covering this topology."
            )

    residence_path: Path | None = None
    if residence_rows:
        residence_path = GENERATED_CALIBRATION / "corridor_residence_calibration.csv"
        pd.DataFrame(residence_rows).to_csv(residence_path, index=False)
    else:
        old = GENERATED_CALIBRATION / "corridor_residence_calibration.csv"
        old.unlink(missing_ok=True)

    metadata = {
        "history_runs": [p.name for p in runs],
        "dark_stations": dark_stations,
        "dwell_rows": int(len(dwell)),
        "corridors": corridor_summaries,
        "causality": "Calibration source is data/calibration/history only; current_run is excluded.",
    }
    (GENERATED_CALIBRATION / "calibration_manifest.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    return dwell_path, residence_path, metadata


def _validate_output(path: Path) -> dict:
    if not path.is_file():
        raise RuntimeError(f"Prediction output was not created: {path}")
    required = {
        "schema_version",
        "timestamp_ms",
        "station_id",
        "route",
        "bottleneck_probability",
        "warning",
        "decision_threshold",
    }
    routes = Counter()
    triggers = Counter()
    invalid_probabilities = 0
    unknown_categories = 0
    s01_predictions = 0
    rows = 0

    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            rows += 1
            missing = required - set(row)
            if missing:
                raise RuntimeError(f"Output line {line_no} missing fields: {sorted(missing)}")
            p = row.get("bottleneck_probability")
            if not isinstance(p, (int, float)) or not math.isfinite(float(p)) or not (0 <= float(p) <= 1):
                invalid_probabilities += 1
            routes[str(row.get("route"))] += 1
            triggers[str(row.get("prediction_trigger"))] += 1
            if str(row.get("station_id")) == "S01":
                s01_predictions += 1
            diagnostics = row.get("diagnostics") or {}
            if diagnostics.get("unknown_categories"):
                unknown_categories += 1

    if rows == 0:
        raise RuntimeError("Pipeline produced zero predictions")
    if invalid_probabilities:
        raise RuntimeError(f"Found {invalid_probabilities} invalid probabilities")
    if unknown_categories:
        raise RuntimeError(f"Found {unknown_categories} unknown model-category outputs")
    if s01_predictions:
        raise RuntimeError(f"Found {s01_predictions} invalid S01 predictions")

    return {
        "predictions": rows,
        "routes": dict(routes),
        "triggers": dict(triggers),
        "invalid_probabilities": invalid_probabilities,
        "unknown_categories": unknown_categories,
        "s01_predictions": s01_predictions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the final bottleneck pipeline on data/input/current_run/."
    )
    parser.add_argument(
        "--dark-stations",
        default=None,
        help="Comma-separated DARK station IDs. If omitted, the launcher prompts you.",
    )
    parser.add_argument("--particles", type=int, default=3000)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--run-id", default="CURRENT_RUN")
    args = parser.parse_args()

    files = _required_current_files()
    stations = _validate_input_schema(files)
    available = stations["station_id"].astype(str).tolist()

    raw_dark = args.dark_stations
    if raw_dark is None:
        print("\nAvailable stations:")
        print(", ".join(available))
        raw_dark = input(
            "\nEnter DARK station IDs separated by commas "
            "(example: S08,S12,S13,S14; blank = all LIGHT): "
        ).strip()
    dark_stations = _parse_dark_stations(raw_dark or "", available)

    print("\n" + "=" * 72)
    print("FINAL BOTTLENECK PREDICTION PIPELINE")
    print("=" * 72)
    print(f"Input folder   : {CURRENT_RUN.relative_to(ROOT)}")
    print(f"DARK stations  : {', '.join(dark_stations) if dark_stations else 'NONE'}")
    print(f"Particles      : {args.particles}")

    # Configure the user's selected topology.
    _run([
        sys.executable,
        "main.py",
        "configure",
        "--stations",
        str(files["stations.csv"]),
        "--output",
        str(CONFIGURED_STATIONS),
        "--dark-stations",
        ",".join(dark_stations),
    ])

    # Build topology-specific calibration from PRIOR runs only.
    dwell_path, residence_path, calibration_meta = _build_prior_calibration(dark_stations)
    if dark_stations:
        print(f"Historical calibration runs: {calibration_meta['history_runs']}")
        print(f"Historical dwell rows      : {calibration_meta['dwell_rows']}")
        for c in calibration_meta["corridors"]:
            print(f"Corridor calibration       : {c['sequence']} -> {c['rows']} rows")

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "main.py",
        "replay",
        "--run-dir",
        str(CURRENT_RUN),
        "--configured-stations",
        str(CONFIGURED_STATIONS),
        "--corridor-particles",
        str(args.particles),
        "--run-id",
        str(args.run_id),
        "--include-diagnostics",
        "--output-jsonl",
        str(output),
    ]
    if dwell_path is not None:
        cmd += ["--historical-dwell", str(dwell_path)]
    if residence_path is not None:
        cmd += ["--corridor-residence", str(residence_path)]
    if STATION_CHECKPOINTS.is_file():
        cmd += ["--station-checkpoints", str(STATION_CHECKPOINTS)]

    _run(cmd)
    summary = _validate_output(output)

    print("\n" + "=" * 72)
    print("PIPELINE VALIDATION: PASS")
    print("=" * 72)
    print(f"Predictions          : {summary['predictions']}")
    print(f"Routes               : {summary['routes']}")
    print(f"Invalid probabilities: {summary['invalid_probabilities']}")
    print(f"Unknown categories   : {summary['unknown_categories']}")
    print(f"S01 predictions      : {summary['s01_predictions']}")
    print(f"Output               : {output.relative_to(ROOT) if output.is_relative_to(ROOT) else output}")
    print("\nCurrent-run calibration leakage: NONE (prior history only).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped by user.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
