"""Thin operational shell for the simulator-to-bottleneck workflow.

This module intentionally delegates to the generator, simulator orchestrator,
factory artifact service, and existing runtime.  It owns paths/confirmation and
does not reimplement simulation, DARK calibration, feature building, or model
inference.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(__file__).resolve().parent
for path in (PROJECT_ROOT, PACKAGE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from factory_models import (  # noqa: E402
    BASE_MODEL_ID,
    DEFAULT_ARTIFACT_ROOT,
    configure_factory,
    delete_model,
    list_models,
    model_paths,
    select_model,
    selected_model_id,
    train_factory_model,
)
from simulation.training.orchestrator import run_generated  # noqa: E402
from simulation.training.scenario_generator import generate  # noqa: E402

if __package__ in (None, ""):
    from factory_registry import (  # noqa: E402
        DEFAULT_REGISTRY,
        delete_factory,
        get_factory,
        list_factories,
        register_factory,
        set_configured_stations,
    )
else:
    from .factory_registry import (  # noqa: E402
        DEFAULT_REGISTRY,
        delete_factory,
        get_factory,
        list_factories,
        register_factory,
        set_configured_stations,
    )


DEFAULT_FACTORY = PROJECT_ROOT / "simulation" / "config" / "factory.json"
DEFAULT_RUNS = PROJECT_ROOT / "simulation" / "training" / "runs"
DEFAULT_GENERATED = PROJECT_ROOT / "simulation" / "training" / "generated"
DEFAULT_SIMULATOR = PROJECT_ROOT / "simulation" / "build" / "Debug" / "simulation.exe"


def _require_force(force: bool, action: str) -> None:
    if not force:
        raise ValueError(f"Refusing to {action}. Repeat with --force after verifying the target.")


def _run_directory(path: str | Path) -> Path:
    run = Path(path).expanduser().resolve()
    required = ("stations.csv", "units.csv", "station_events.csv")
    missing = [name for name in required if not (run / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Not a completed simulator run directory: {run}; missing {', '.join(missing)}")
    return run


def _model_runtime_args(model_id: str | None, artifact_root: Path) -> list[str]:
    chosen = model_id or selected_model_id(artifact_root)
    if chosen == BASE_MODEL_ID:
        raise ValueError(
            "The protected base model has no factory topology/calibration state. "
            "Select a trained factory artifact before a DARK-capable runtime test."
        )
    paths = model_paths(chosen, artifact_root)
    args = [
        "--configured-stations", str(paths["configured_stations"]),
        "--model-bundle", str(paths["model_bundle"]),
    ]
    if "historical_dwell" in paths:
        args.extend(["--historical-dwell", str(paths["historical_dwell"])])
    if "corridor_residence" in paths:
        args.extend(["--corridor-residence", str(paths["corridor_residence"])])
    return args


def command_configure(args: argparse.Namespace) -> int:
    print(configure_factory(args.factory, args.stations, args.output))
    return 0


def command_generate(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Generation output already contains files: {output}. Choose a new --output directory.")
    print(generate(args.factory, args.output, args.count, args.seed, args.duration_ms))
    return 0


def command_simulate(args: argparse.Namespace) -> int:
    generated = Path(args.generated).expanduser().resolve()
    manifest = generated / "manifest.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"Generated scenario manifest not found: {manifest}")
    output = Path(args.output).expanduser().resolve()
    run_ids = [str(item["run_id"]) for item in json.loads(manifest.read_text(encoding="utf-8")).get("runs", [])]
    existing = [run_id for run_id in run_ids if (output / run_id).exists()]
    if existing:
        raise FileExistsError("Simulator output already exists for: " + ", ".join(existing) + ". Choose a new --output directory.")
    print(run_generated(args.simulator, args.factory, args.generated, args.output, args.fail_fast))
    return 0


def command_data_list(args: argparse.Namespace) -> int:
    root = Path(args.runs).expanduser().resolve()
    if not root.is_dir():
        print("[]")
        return 0
    rows = []
    for run in sorted(path for path in root.glob("run_*") if path.is_dir()):
        metadata = run / "run_metadata.json"
        data = json.loads(metadata.read_text(encoding="utf-8")) if metadata.is_file() else {}
        rows.append({"run_id": run.name, "path": str(run), "completed": metadata.is_file(), "units_created": data.get("units_created")})
    print(json.dumps(rows, indent=2))
    return 0


def command_data_delete(args: argparse.Namespace) -> int:
    _require_force(args.force, f"delete run {args.run_id!r}")
    root = Path(args.runs).expanduser().resolve()
    target = (root / args.run_id).resolve()
    if target.parent != root or not target.is_dir() or not target.name.startswith("run_"):
        raise ValueError("Run deletion target must be an existing direct run_* child of --runs")
    shutil.rmtree(target)
    print(f"Deleted run directory: {target}")
    return 0


def command_models_list(args: argparse.Namespace) -> int:
    print(json.dumps(list_models(args.artifact_root), indent=2))
    return 0


def command_models_select(args: argparse.Namespace) -> int:
    print(json.dumps(select_model(args.model_id, args.artifact_root), indent=2))
    return 0


def command_models_delete(args: argparse.Namespace) -> int:
    _require_force(args.force, f"delete model {args.model_id!r}")
    delete_model(args.model_id, args.artifact_root)
    print(f"Deleted factory model: {args.model_id}")
    return 0


def command_train(args: argparse.Namespace) -> int:
    if args.replace:
        _require_force(args.force, f"replace model {args.model_id!r}")
    registered = get_factory(args.factory_id, args.registry) if args.factory_id else None
    result = train_factory_model(
        model_id=args.model_id,
        factory_json=registered["factory_json"] if registered else args.factory,
        runs_root=args.runs,
        configured_stations=registered.get("configured_stations") if registered else None,
        root=args.artifact_root,
        seed=args.seed,
        threshold_objective=args.threshold_objective,
        replace=args.replace,
    )
    print(result)
    return 0


def command_factories_list(args: argparse.Namespace) -> int:
    print(json.dumps(list_factories(args.registry), indent=2))
    return 0


def command_factories_show(args: argparse.Namespace) -> int:
    print(json.dumps(get_factory(args.factory_id, args.registry), indent=2))
    return 0


def command_factories_register(args: argparse.Namespace) -> int:
    print(json.dumps(register_factory(args.factory_id, args.factory, args.registry, replace=args.replace), indent=2))
    return 0


def command_factories_configure(args: argparse.Namespace) -> int:
    entry = get_factory(args.factory_id, args.registry)
    output = args.output or (Path(args.registry).expanduser().resolve().parent / "configurations" / entry["id"] / "configured_stations.csv")
    configured = configure_factory(entry["factory_json"], args.stations, output)
    print(json.dumps(set_configured_stations(entry["id"], configured, args.registry), indent=2))
    return 0


def command_factories_delete(args: argparse.Namespace) -> int:
    _require_force(args.force, f"delete factory registration {args.factory_id!r}")
    delete_factory(args.factory_id, args.registry)
    print(f"Deleted factory registration: {args.factory_id}")
    return 0


def command_run_prescribed(args: argparse.Namespace) -> int:
    # Delegate to the existing replay command so CSV, event streaming, and
    # inference use exactly the same process_event/advance_time pipeline.
    from main import main as bottleneck_main

    run = _run_directory(args.run_dir)
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        _require_force(args.force, f"overwrite prediction output {str(output)!r}")
    argv = ["replay", "--run-dir", str(run), "--output-jsonl", str(output), "--run-id", args.run_id or run.name]
    argv.extend(_model_runtime_args(args.model_id, args.artifact_root))
    if not args.unpaced:
        argv.extend(["--pace", "--mult", str(args.mult)])
    return bottleneck_main(argv)


def command_run_random(args: argparse.Namespace) -> int:
    generated = Path(args.generated).expanduser().resolve()
    runs = Path(args.runs).expanduser().resolve()
    if generated.exists() and any(generated.iterdir()):
        raise FileExistsError(
            f"Random-test generated-input directory already contains files: {generated}. "
            "Choose a new --generated directory."
        )
    if (runs / "run_0001").exists():
        raise FileExistsError(
            f"Random-test destination already exists: {runs / 'run_0001'}. "
            "Choose a new --runs directory rather than overwriting a completed run."
        )
    generate(args.factory, generated, 1, args.seed, args.duration_ms)
    run_generated(args.simulator, args.factory, generated, runs, fail_fast=True)
    prescribed = argparse.Namespace(
        run_dir=runs / "run_0001", output=args.output, run_id="random_run_0001",
        model_id=args.model_id, artifact_root=args.artifact_root, mult=args.mult,
        unpaced=args.unpaced, force=args.force,
    )
    return command_run_prescribed(prescribed)


def command_status(args: argparse.Namespace) -> int:
    records = list_models(args.artifact_root)
    print(json.dumps({
        "selected_model": selected_model_id(args.artifact_root),
        "models": records,
        "factories": list_factories(args.registry),
        "default_factory": str(DEFAULT_FACTORY),
        "default_runs": str(DEFAULT_RUNS),
        "simulator": str(DEFAULT_SIMULATOR),
    }, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Digital Twin internal operations shell")
    sub = parser.add_subparsers(dest="command", required=True)

    configure = sub.add_parser("configure", help="Create a factory-specific station configuration")
    configure.add_argument("--factory", type=Path, default=DEFAULT_FACTORY)
    configure.add_argument("--stations", type=Path, required=True)
    configure.add_argument("--output", type=Path, required=True)
    configure.set_defaults(func=command_configure)

    factories = sub.add_parser("factories", help="Register factory definitions and retain their station configuration")
    factories_sub = factories.add_subparsers(dest="factories_command", required=True)
    factories_list = factories_sub.add_parser("list", help="List registered factories")
    factories_list.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    factories_list.set_defaults(func=command_factories_list)
    factories_show = factories_sub.add_parser("show", help="Show one registered factory")
    factories_show.add_argument("factory_id")
    factories_show.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    factories_show.set_defaults(func=command_factories_show)
    factories_register = factories_sub.add_parser("register", help="Register a factory.json definition")
    factories_register.add_argument("factory_id")
    factories_register.add_argument("factory", type=Path)
    factories_register.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    factories_register.add_argument("--replace", action="store_true")
    factories_register.set_defaults(func=command_factories_register)
    factories_configure = factories_sub.add_parser("configure", help="Create and record configured stations for a registered factory")
    factories_configure.add_argument("factory_id")
    factories_configure.add_argument("--stations", type=Path, required=True)
    factories_configure.add_argument("--output", type=Path)
    factories_configure.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    factories_configure.set_defaults(func=command_factories_configure)
    factories_delete = factories_sub.add_parser("delete", help="Remove a factory registration (does not delete its files)")
    factories_delete.add_argument("factory_id")
    factories_delete.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    factories_delete.add_argument("--force", action="store_true")
    factories_delete.set_defaults(func=command_factories_delete)

    generate_parser = sub.add_parser("generate", help="Generate factory-specific random training/test scenarios")
    generate_parser.add_argument("--factory", type=Path, default=DEFAULT_FACTORY)
    generate_parser.add_argument("--output", type=Path, default=DEFAULT_GENERATED)
    generate_parser.add_argument("--count", type=int, required=True)
    generate_parser.add_argument("--seed", type=int, default=42)
    generate_parser.add_argument("--duration-ms", type=int, default=28_800_000)
    generate_parser.set_defaults(func=command_generate)

    simulate = sub.add_parser("simulate", help="Execute a generated manifest into independent run directories")
    simulate.add_argument("--simulator", type=Path, default=DEFAULT_SIMULATOR)
    simulate.add_argument("--factory", type=Path, default=DEFAULT_FACTORY)
    simulate.add_argument("--generated", type=Path, default=DEFAULT_GENERATED)
    simulate.add_argument("--output", type=Path, default=DEFAULT_RUNS)
    simulate.add_argument("--fail-fast", action="store_true")
    simulate.set_defaults(func=command_simulate)

    data = sub.add_parser("data", help="Inspect or delete completed simulator runs")
    data_sub = data.add_subparsers(dest="data_command", required=True)
    data_list = data_sub.add_parser("list")
    data_list.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    data_list.set_defaults(func=command_data_list)
    data_delete = data_sub.add_parser("delete")
    data_delete.add_argument("run_id")
    data_delete.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    data_delete.add_argument("--force", action="store_true")
    data_delete.set_defaults(func=command_data_delete)

    models = sub.add_parser("models", help="Inspect, select, or delete model artifacts")
    models_sub = models.add_subparsers(dest="models_command", required=True)
    for name, func in (("list", command_models_list), ("select", command_models_select), ("delete", command_models_delete)):
        child = models_sub.add_parser(name)
        child.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
        if name != "list":
            child.add_argument("model_id")
        if name == "delete":
            child.add_argument("--force", action="store_true")
        child.set_defaults(func=func)

    train = sub.add_parser("train", help="Train and publish one immutable factory model artifact")
    train.add_argument("model_id")
    train.add_argument("--factory", type=Path, default=DEFAULT_FACTORY)
    train.add_argument("--factory-id", help="Use the factory.json registered under this factory ID")
    train.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    train.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    train.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    train.add_argument("--seed", type=int, default=42)
    train.add_argument("--threshold-objective", choices=("f1", "f2"), default="f2")
    train.add_argument("--replace", action="store_true")
    train.add_argument("--force", action="store_true")
    train.set_defaults(func=command_train)

    run = sub.add_parser("run", help="Run causal prescribed or random tests using a selected factory artifact")
    run_sub = run.add_subparsers(dest="run_command", required=True)
    prescribed = run_sub.add_parser("prescribed")
    prescribed.add_argument("--run-dir", type=Path, required=True)
    prescribed.add_argument("--output", type=Path, required=True)
    prescribed.add_argument("--run-id")
    prescribed.add_argument("--model-id")
    prescribed.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    prescribed.add_argument("--mult", type=float, default=60.0, help="Causal event-delivery multiplier when paced")
    prescribed.add_argument("--unpaced", action="store_true", help="Process as fast as possible instead of pacing at MULT")
    prescribed.add_argument("--force", action="store_true", help="Allow overwriting --output")
    prescribed.set_defaults(func=command_run_prescribed)
    random_run = run_sub.add_parser("random")
    random_run.add_argument("--factory", type=Path, default=DEFAULT_FACTORY)
    random_run.add_argument("--simulator", type=Path, default=DEFAULT_SIMULATOR)
    random_run.add_argument("--generated", type=Path, default=DEFAULT_GENERATED / "random_test")
    random_run.add_argument("--runs", type=Path, default=DEFAULT_RUNS / "random_test")
    random_run.add_argument("--output", type=Path, required=True)
    random_run.add_argument("--model-id")
    random_run.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    random_run.add_argument("--seed", type=int, default=42)
    random_run.add_argument("--duration-ms", type=int, default=28_800_000)
    random_run.add_argument("--mult", type=float, default=60.0)
    random_run.add_argument("--unpaced", action="store_true")
    random_run.add_argument("--force", action="store_true", help="Allow overwriting --output")
    random_run.set_defaults(func=command_run_random)

    status = sub.add_parser("status", help="Show selected model and discovered default paths")
    status.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    status.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    status.set_defaults(func=command_status)
    shell = sub.add_parser("shell", help="Start the interactive, cross-platform Python shell")
    shell.set_defaults(func=lambda _: interactive_shell(parser))
    return parser


def interactive_shell(parser: argparse.ArgumentParser) -> int:
    """Run a small command shell without relying on PowerShell, bash, or cmd.exe."""
    print("Digital Twin shell. Type 'help' for commands, or 'quit' to exit.")
    while True:
        try:
            line = input("digital-twin> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line.lower() in {"quit", "exit"}:
            return 0
        if line.lower() in {"help", "?"}:
            parser.print_help()
            continue
        try:
            # Forward-slash paths work on every supported platform and POSIX
            # tokenisation gives quoted names the same behaviour everywhere.
            args = parser.parse_args(shlex.split(line))
            if args.command == "shell":
                print("Already in the interactive shell.")
            else:
                args.func(args)
        except SystemExit:
            # argparse has already printed a concise command-specific error.
            continue
        except Exception as error:
            print(f"ERROR: {error}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is not None and not argv:
        return interactive_shell(parser)
    if argv is None and len(sys.argv) == 1:
        return interactive_shell(parser)
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as error:
        parser.exit(2, f"ERROR: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
