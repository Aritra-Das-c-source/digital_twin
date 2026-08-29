"""Factory-specific immutable bottleneck model artifacts.

An artifact contains learned state only: the frozen XGBoost bundle, its feature
contract/category mappings/threshold, factory station configuration, and DARK
historical calibration.  Runtime queues, particle filters, observations, and
test-run output never enter this directory.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

if __package__ in (None, ""):
    from config.configure_stations import configure_sensor_coverage
    from runtime.runtime_controller import derive_dark_topology
    from training.build_bottleneck_dataset import discover_runs, materialize
else:
    from .config.configure_stations import configure_sensor_coverage
    from .runtime.runtime_controller import derive_dark_topology
    from .training.build_bottleneck_dataset import discover_runs, materialize


ROOT = Path(__file__).resolve().parent
DEFAULT_ARTIFACT_ROOT = ROOT / "factory_models"
BASE_MODEL_DIR = ROOT / "ml" / "bottleneck_model" / "bottleneck_model_artifacts"
BASE_MODEL_ID = "base"
SELECTION_FILE = "selected_model.json"
ARTIFACT_FILE = "artifact.json"


def _safe_model_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value).strip()).strip("-_").lower()
    if not cleaned:
        raise ValueError("Factory/model id must contain a letter or number")
    if cleaned == BASE_MODEL_ID:
        raise ValueError(f"{BASE_MODEL_ID!r} is reserved for the protected initial model")
    return cleaned


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def artifact_root(root: str | Path = DEFAULT_ARTIFACT_ROOT) -> Path:
    path = Path(root).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _base_paths() -> dict[str, Path]:
    bundle = BASE_MODEL_DIR / "bottleneck_model_bundle.joblib"
    xgb = BASE_MODEL_DIR / "bottleneck_xgboost.json"
    if not bundle.is_file() or not xgb.is_file():
        raise FileNotFoundError("Protected initial model is incomplete under " + str(BASE_MODEL_DIR))
    return {"bundle": bundle.resolve(), "xgb": xgb.resolve()}


def _artifact_directory(model_id: str, root: str | Path = DEFAULT_ARTIFACT_ROOT) -> Path:
    return artifact_root(root) / _safe_model_id(model_id)


def list_models(root: str | Path = DEFAULT_ARTIFACT_ROOT) -> list[dict[str, Any]]:
    store = artifact_root(root)
    base = _base_paths()
    selected = selected_model_id(store)
    records: list[dict[str, Any]] = [{
        "id": BASE_MODEL_ID,
        "kind": "initial_base",
        "protected": True,
        "selected": selected == BASE_MODEL_ID,
        "bundle": str(base["bundle"]),
        "trained_at_utc": None,
    }]
    for directory in sorted(path for path in store.iterdir() if path.is_dir() and not path.name.startswith(".")):
        manifest = directory / ARTIFACT_FILE
        if not manifest.is_file():
            continue
        data = _read_json(manifest)
        records.append({
            "id": data["model_id"],
            "kind": "factory_trained",
            "protected": False,
            "selected": selected == data["model_id"],
            "bundle": str(directory / data["paths"]["model_bundle"]),
            "trained_at_utc": data.get("trained_at_utc"),
            "run_count": data.get("training", {}).get("run_count"),
        })
    return records


def selected_model_id(root: str | Path = DEFAULT_ARTIFACT_ROOT) -> str:
    selection = artifact_root(root) / SELECTION_FILE
    if not selection.is_file():
        return BASE_MODEL_ID
    return str(_read_json(selection).get("model_id", BASE_MODEL_ID))


def select_model(model_id: str, root: str | Path = DEFAULT_ARTIFACT_ROOT) -> dict[str, Any]:
    resolved = BASE_MODEL_ID if model_id == BASE_MODEL_ID else _safe_model_id(model_id)
    model_paths(resolved, root)  # Verify before mutating selection.
    _write_json(artifact_root(root) / SELECTION_FILE, {
        "schema_version": "1.0",
        "model_id": resolved,
        "selected_at_utc": datetime.now(UTC).isoformat(),
    })
    return describe_model(resolved, root)


def describe_model(model_id: str, root: str | Path = DEFAULT_ARTIFACT_ROOT) -> dict[str, Any]:
    if model_id == BASE_MODEL_ID:
        paths = _base_paths()
        return {"model_id": BASE_MODEL_ID, "protected": True, "paths": {"model_bundle": str(paths["bundle"]), "xgboost_model": str(paths["xgb"])}}
    directory = _artifact_directory(model_id, root)
    manifest = directory / ARTIFACT_FILE
    if not manifest.is_file():
        raise FileNotFoundError(f"Factory model artifact not found: {model_id}")
    return _read_json(manifest)


def model_paths(model_id: str | None = None, root: str | Path = DEFAULT_ARTIFACT_ROOT) -> dict[str, Path]:
    resolved = model_id or selected_model_id(root)
    if resolved == BASE_MODEL_ID:
        return _base_paths()
    manifest = describe_model(resolved, root)
    directory = _artifact_directory(resolved, root)
    paths = {name: (directory / relative).resolve() for name, relative in manifest["paths"].items()}
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Factory artifact {resolved!r} is incomplete: {', '.join(missing)}")
    return paths


def delete_model(model_id: str, root: str | Path = DEFAULT_ARTIFACT_ROOT) -> None:
    if model_id == BASE_MODEL_ID:
        raise PermissionError("The protected initial/base model cannot be deleted")
    directory = _artifact_directory(model_id, root)
    if not directory.is_dir():
        raise FileNotFoundError(f"Factory model artifact not found: {model_id}")
    if selected_model_id(root) == _safe_model_id(model_id):
        raise ValueError("Cannot delete the selected model; select another model first")
    shutil.rmtree(directory)


def _dark_station_ids(factory_json: Path) -> set[str]:
    factory = _read_json(factory_json)
    station_ids: set[str] = set()
    for zone in factory.get("darkZones", []):
        start, end = zone.get("startStationId"), zone.get("endStationId")
        if not isinstance(start, int) or not isinstance(end, int) or start > end:
            raise ValueError("factory.json contains an invalid dark-zone station range")
        station_ids.update(f"S{value + 1:02d}" for value in range(start, end + 1))
    return station_ids


def configure_factory(factory_json: str | Path, stations_csv: str | Path, output: str | Path) -> Path:
    """Create a factory-specific configured-stations file without editing raw runs."""
    factory = Path(factory_json).expanduser().resolve()
    stations_path = Path(stations_csv).expanduser().resolve()
    if not factory.is_file() or not stations_path.is_file():
        raise FileNotFoundError("Factory JSON and stations.csv must both exist")
    stations = pd.read_csv(stations_path)
    if "station_id" not in stations.columns:
        raise ValueError("stations.csv must contain station_id")
    stations["station_id"] = stations["station_id"].astype(str).str.strip()
    dark_ids = _dark_station_ids(factory)
    unknown = dark_ids - set(stations["station_id"])
    if unknown:
        raise ValueError("factory dark-zone stations missing from stations.csv: " + ", ".join(sorted(unknown)))
    configured = configure_sensor_coverage(stations, dark_ids)
    result = Path(output).expanduser().resolve()
    result.parent.mkdir(parents=True, exist_ok=True)
    configured.to_csv(result, index=False)
    return result


def _boundary_calibration_rows(
    runs: list[Path], configured: pd.DataFrame
) -> tuple[list[pd.DataFrame], list[dict[str, Any]], list[dict[str, Any]]]:
    """Recover DARK-corridor history from the simulator's public boundaries.

    The C++ simulator deliberately omits internal DARK processing rows.  Its
    observable contract is a ``DARK_ZONE_ENTERED`` event at the first hidden
    station and a ``DARK_ZONE_EXITED`` event at the downstream LIGHT station.
    Those paired events are the real completed corridor interval; attempting to
    derive hidden ``PROCESSING_STARTED``/``PROCESSING_COMPLETED`` rows always
    produces an empty calibration file.
    """
    _, corridors = derive_dark_topology(configured)
    dwell_parts: list[pd.DataFrame] = []
    residence_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    for corridor in corridors.values():
        if corridor.downstream_light_station is None:
            continue
        corridor_rows: list[dict[str, Any]] = []
        for run in runs:
            events = pd.read_csv(run / "station_events.csv")
            units = pd.read_csv(run / "units.csv")
            events["station_id"] = events["station_id"].astype(str).str.strip()
            events["unit_id"] = events["unit_id"].astype(str)
            events["event_type"] = events["event_type"].astype(str).str.upper()
            variants = dict(zip(units["unit_id"].astype(str), units["vehicle_model"].astype(str)))
            entries = events[
                events["station_id"].eq(corridor.first_station)
                & events["event_type"].eq("DARK_ZONE_ENTERED")
            ][["unit_id", "timestamp_ms"]].rename(columns={"timestamp_ms": "entry_ms"})
            exits = events[
                events["station_id"].eq(corridor.downstream_light_station)
                & events["event_type"].eq("DARK_ZONE_EXITED")
            ][["unit_id", "timestamp_ms"]].rename(columns={"timestamp_ms": "exit_ms"})
            completed = entries.merge(exits, on="unit_id", how="inner")
            completed = completed[completed["exit_ms"] > completed["entry_ms"]]
            if completed.empty:
                continue

            entry_times = sorted(completed["entry_ms"].astype(int).tolist())
            exit_times = sorted(completed["exit_ms"].astype(int).tolist())
            for row in completed.itertuples(index=False):
                entry_ms, exit_ms = int(row.entry_ms), int(row.exit_ms)
                # Count only units that entered before this boundary and had
                # not already exited. This is causal corridor load history.
                active = sum(t < entry_ms for t in entry_times) - sum(t < entry_ms for t in exit_times)
                corridor_rows.append({
                    "station_id": corridor.first_station,
                    "variant": variants.get(str(row.unit_id), "__UNKNOWN__"),
                    "entry_ts": pd.to_datetime(entry_ms, unit="ms", utc=True).isoformat(),
                    "exit_ts": pd.to_datetime(exit_ms, unit="ms", utc=True).isoformat(),
                    "corridor_load": active,
                    "source_run": run.name,
                    "corridor_first_station": corridor.first_station,
                    "corridor_upstream_station": corridor.upstream_light_station or "",
                    "boundary_source": "DARK_ZONE_ENTERED/DARK_ZONE_EXITED",
                })
        if corridor_rows:
            part = pd.DataFrame(corridor_rows)
            dwell_parts.append(part[["station_id", "variant", "entry_ts", "exit_ts", "source_run"]])
            residence_rows.extend(corridor_rows)
            summaries.append({
                "zone_id": corridor.zone_id,
                "sequence": list(corridor.sequence),
                "rows": len(corridor_rows),
                "source": "simulator_dark_boundaries",
            })
    return dwell_parts, residence_rows, summaries


def _build_dark_calibration(
    runs: list[Path],
    configured_csv: Path,
    output_dir: Path,
    *,
    dark_station_ids: set[str],
) -> dict[str, Any]:
    """Build calibration only for the DARK topology declared by factory.json.

    ``factory.json`` is the factory's source of truth.  A stale configured
    stations file must never cause a LIGHT-only factory to enter the DARK
    calibration path.
    """
    configured = pd.read_csv(configured_csv)
    dark_ids = set(dark_station_ids)
    if not dark_ids:
        return {
            "dark_station_ids": [],
            "dwell": None,
            "corridor_residence": None,
            "history_run_ids": [run.name for run in runs],
        }

    configured_dark_ids = set(configured.loc[
        configured["sensor_coverage"].astype(str).str.upper().eq("NONE"), "station_id"
    ].astype(str))
    if configured_dark_ids != dark_ids:
        raise ValueError(
            "configured stations DARK topology does not match factory.json; "
            "run 'factory configure' again before training"
        )

    # Existing direct station calibration remains valid for integrations that
    # expose DARK station processing events. The bundled C++ simulator instead
    # exposes only DARK-zone boundary events; handle that contract below.
    dark_module_dir = str(ROOT / "dark_zone")
    if dark_module_dir not in sys.path:
        sys.path.insert(0, dark_module_dir)
    from csv_adapter import derive_historical_dwell_csv  # type: ignore
    from build_corridor_residence_calibration import build_one_run  # type: ignore

    output_dir.mkdir(parents=True, exist_ok=True)
    has_internal_dark_processing = False
    for run in runs:
        events = pd.read_csv(run / "station_events.csv", usecols=["station_id", "event_type"])
        has_internal_dark_processing = bool((
            events["station_id"].astype(str).isin(dark_ids)
            & events["event_type"].astype(str).isin({"PROCESSING_STARTED", "PROCESSING_COMPLETED"})
        ).any())
        if has_internal_dark_processing:
            break

    dwell_parts: list[pd.DataFrame] = []
    residence_rows: list[dict[str, Any]] = []
    corridor_summary: list[dict[str, Any]] = []
    calibration_source = "internal_station_processing"
    if has_internal_dark_processing:
        for run in runs:
            temporary = output_dir / f".dwell-{run.name}.csv"
            part = derive_historical_dwell_csv(str(run / "station_events.csv"), str(run / "units.csv"), str(temporary), dark_zone_station_ids=dark_ids)
            temporary.unlink(missing_ok=True)
            if not part.empty:
                dwell_parts.append(part.assign(source_run=run.name))
        _, corridors = derive_dark_topology(configured)
        for corridor in corridors.values():
            rows = [row for run in runs for row in build_one_run(run, list(corridor.sequence), upstream_station=corridor.upstream_light_station)]
            if not rows:
                raise ValueError(f"No completed DARK corridor intervals were found for {list(corridor.sequence)}")
            residence_rows.extend(rows)
            corridor_summary.append({"zone_id": corridor.zone_id, "sequence": list(corridor.sequence), "rows": len(rows)})
    else:
        calibration_source = "simulator_dark_boundaries"
        dwell_parts, residence_rows, corridor_summary = _boundary_calibration_rows(runs, configured)

    if not dwell_parts:
        raise ValueError(
            "No completed DARK intervals were found. DARK factories require either "
            "internal processing events or paired DARK_ZONE_ENTERED/DARK_ZONE_EXITED events."
        )
    dwell_path = output_dir / "historical_dwell.csv"
    pd.concat(dwell_parts, ignore_index=True).to_csv(dwell_path, index=False)

    residence_path: Path | None = None
    if residence_rows:
        residence_path = output_dir / "corridor_residence_calibration.csv"
        pd.DataFrame(residence_rows).to_csv(residence_path, index=False)
    return {
        "dark_station_ids": sorted(dark_ids),
        "dwell": "calibration/historical_dwell.csv",
        "corridor_residence": "calibration/corridor_residence_calibration.csv" if residence_path else None,
        "history_run_ids": [run.name for run in runs],
        "dwell_rows": int(sum(len(part) for part in dwell_parts)),
        "corridors": corridor_summary,
        "source": calibration_source,
        "causality": "Calibration uses only completed training runs; runtime/test runs are never included.",
    }


def train_factory_model(
    *,
    model_id: str,
    factory_json: str | Path,
    runs_root: str | Path,
    configured_stations: str | Path | None = None,
    root: str | Path = DEFAULT_ARTIFACT_ROOT,
    seed: int = 42,
    threshold_objective: str = "f2",
    replace: bool = False,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Create an independent factory artifact from immutable base state and run folders."""
    safe_id = _safe_model_id(model_id)
    store = artifact_root(root)
    destination = store / safe_id
    if destination.exists() and not replace:
        raise FileExistsError(f"Factory artifact already exists: {destination}; choose another id or explicitly replace it")
    if destination.exists() and selected_model_id(store) == safe_id:
        raise ValueError("Cannot replace the currently selected factory model")

    factory = Path(factory_json).expanduser().resolve()
    if not factory.is_file():
        raise FileNotFoundError(f"Factory JSON not found: {factory}")
    runs = discover_runs(runs_root)
    base = _base_paths()  # Selection is intentionally never consulted here.
    staging = store / f".{safe_id}.staging-{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        if progress:
            progress(f"Training {safe_id}: preparing {len(runs)} completed simulator run(s)...")
        configured = staging / "configured_stations.csv"
        if configured_stations is None:
            configure_factory(factory, runs[0] / "stations.csv", configured)
        else:
            source_configured = Path(configured_stations).expanduser().resolve()
            if not source_configured.is_file():
                raise FileNotFoundError(f"Configured stations CSV not found: {source_configured}")
            shutil.copy2(source_configured, configured)
        if progress:
            progress("Training: materializing causal bottleneck features (this can take a while)...")
        derived = materialize(runs_root, staging / "derived")
        model_dir = staging / "model"
        trainer = ROOT / "ml" / "bottleneck_model" / "train_bottleneck_xgboost.py"
        command = [
            sys.executable, str(trainer), "--dataset", str(derived), "--output", str(model_dir),
            "--seed", str(seed), "--threshold-objective", threshold_objective,
            "--base-model", str(base["xgb"]),
        ]
        if progress:
            progress("Training: fitting the factory model from the protected base model (this can take a while)...")
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        (staging / "training.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (staging / "training.stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode:
            raise RuntimeError(f"Bottleneck training failed (see {staging / 'training.stderr.log'})")

        factory_dark_station_ids = _dark_station_ids(factory)
        if progress:
            action = "building DARK calibration" if factory_dark_station_ids else "skipping DARK calibration (factory has no DARK zones)"
            progress(f"Training: {action}...")
        calibration = _build_dark_calibration(
            runs,
            configured,
            staging / "calibration",
            dark_station_ids=factory_dark_station_ids,
        )
        metrics_path = model_dir / "metrics.json"
        metrics = _read_json(metrics_path) if metrics_path.is_file() else {}
        # Split metrics remain useful development diagnostics, but held-out
        # predictions and release-only reports are not factory runtime state.
        # Preserve the metrics in manifest metadata and publish only files the
        # runtime actually needs to load/swap a model.
        for diagnostic in ("metrics.json", "test_predictions.csv", "feature_importance_gain.csv"):
            (model_dir / diagnostic).unlink(missing_ok=True)
        paths = {
            "model_bundle": "model/bottleneck_model_bundle.joblib",
            "xgboost_model": "model/bottleneck_xgboost.json",
            "configured_stations": "configured_stations.csv",
        }
        if calibration["dwell"]:
            paths["historical_dwell"] = calibration["dwell"]
        if calibration["corridor_residence"]:
            paths["corridor_residence"] = calibration["corridor_residence"]
        manifest = {
            "schema_version": "factory-bottleneck-artifact-v1",
            "model_id": safe_id,
            "trained_at_utc": datetime.now(UTC).isoformat(),
            "protected": False,
            "base_model": {"id": BASE_MODEL_ID, "xgboost_sha256": _sha256(base["xgb"]), "bundle_sha256": _sha256(base["bundle"])},
            "factory": {
                "path": str(factory),
                "sha256": _sha256(factory),
                "dark_station_ids": sorted(factory_dark_station_ids),
            },
            "training": {"runs_root": str(Path(runs_root).expanduser().resolve()), "run_count": len(runs), "run_ids": [run.name for run in runs], "seed": seed, "threshold_objective": threshold_objective, "metrics": metrics},
            "calibration": calibration,
            "dark_calibration": calibration if factory_dark_station_ids else None,
            "paths": paths,
            "state_boundary": {"included": "model, feature contract/category mappings, threshold, configured station topology, and DARK historical calibration when DARK stations exist", "excluded": "runtime queues, particle filters, recent observations, clock state, raw run CSVs, and predictions"},
        }
        _write_json(staging / ARTIFACT_FILE, manifest)
        if progress:
            progress("Training: publishing immutable factory artifact...")
        shutil.rmtree(staging / "derived")  # Derived features are reproducible from the source runs.
        if destination.exists():
            shutil.rmtree(destination)
        staging.replace(destination)
        if progress:
            progress(f"Training complete: {destination}")
        return destination
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
