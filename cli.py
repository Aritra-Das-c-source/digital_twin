"""Repository-level Digital Twin control plane.

Run ``py cli.py`` on Windows or ``python3 cli.py`` on macOS/Linux. Running
without arguments opens the interactive shell; one-shot commands use these
same handlers for automation.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = PROJECT_ROOT / "bottlenecks_prediction"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from factory_models import (  # noqa: E402
    BASE_MODEL_ID, DEFAULT_ARTIFACT_ROOT, configure_factory, delete_model,
    list_models, model_paths, select_model, selected_model_id, train_factory_model,
)
from factory_registry import (  # noqa: E402
    DEFAULT_REGISTRY, delete_factory, get_factory, list_factories,
    register_factory, set_configured_stations,
)
from simulation.training.orchestrator import run_generated  # noqa: E402
from simulation.training.scenario_generator import generate  # noqa: E402

DEFAULT_FACTORY = PROJECT_ROOT / "simulation" / "config" / "factory.json"
DEFAULT_RUNS = PROJECT_ROOT / "simulation" / "training" / "runs"
DEFAULT_GENERATED = PROJECT_ROOT / "simulation" / "training" / "generated"
DEFAULT_SIMULATOR = PROJECT_ROOT / "simulation" / "build" / "Debug" / "simulation.exe"


def _require_force(force: bool, action: str) -> None:
    if not force:
        raise ValueError(f"Refusing to {action}. Repeat with --force after verifying the target.")


def _run_directory(path: str | Path) -> Path:
    run = Path(path).expanduser().resolve()
    missing = [name for name in ("stations.csv", "units.csv", "station_events.csv") if not (run / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Not a completed simulator run directory: {run}; missing {', '.join(missing)}")
    return run


def _model_runtime_args(model_id: str | None, artifact_root: Path) -> list[str]:
    chosen = model_id or selected_model_id(artifact_root)
    if chosen == BASE_MODEL_ID:
        raise ValueError("Select a trained factory model before running the bottleneck runtime.")
    paths = model_paths(chosen, artifact_root)
    result = ["--configured-stations", str(paths["configured_stations"]), "--model-bundle", str(paths["model_bundle"])]
    if "historical_dwell" in paths:
        result += ["--historical-dwell", str(paths["historical_dwell"])]
    if "corridor_residence" in paths:
        result += ["--corridor-residence", str(paths["corridor_residence"])]
    return result


def command_configure(args: argparse.Namespace) -> int:
    print(configure_factory(args.factory, args.stations, args.output)); return 0


def command_generate(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Generation output already contains files: {output}. Choose a new --output directory.")
    print(generate(args.factory, output, args.count, args.seed, args.duration_ms, progress=print)); return 0


def command_simulate(args: argparse.Namespace) -> int:
    generated, output = Path(args.generated).expanduser().resolve(), Path(args.output).expanduser().resolve()
    manifest = generated / "manifest.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"Generated scenario manifest not found: {manifest}")
    run_ids = [str(item["run_id"]) for item in json.loads(manifest.read_text(encoding="utf-8")).get("runs", [])]
    existing = [run_id for run_id in run_ids if (output / run_id).exists()]
    if existing:
        raise FileExistsError("Simulator output already exists for: " + ", ".join(existing) + ". Choose a new --output directory.")
    print(run_generated(args.simulator, args.factory, generated, output, args.fail_fast, progress=print)); return 0


def command_data_list(args: argparse.Namespace) -> int:
    root = Path(args.runs).expanduser().resolve()
    if not root.is_dir():
        print("[]"); return 0
    rows = []
    for run in sorted(path for path in root.glob("run_*") if path.is_dir()):
        metadata = run / "run_metadata.json"
        data = json.loads(metadata.read_text(encoding="utf-8")) if metadata.is_file() else {}
        rows.append({"run_id": run.name, "path": str(run), "completed": metadata.is_file(), "units_created": data.get("units_created")})
    print(json.dumps(rows, indent=2)); return 0


def command_data_delete(args: argparse.Namespace) -> int:
    _require_force(args.force, f"delete run {args.run_id!r}")
    root, target = Path(args.runs).expanduser().resolve(), (Path(args.runs).expanduser().resolve() / args.run_id).resolve()
    if target.parent != root or not target.is_dir() or not target.name.startswith("run_"):
        raise ValueError("Run deletion target must be an existing direct run_* child of --runs")
    shutil.rmtree(target); print(f"Deleted run directory: {target}"); return 0


def command_models_list(args: argparse.Namespace) -> int:
    print(json.dumps(list_models(args.artifact_root), indent=2)); return 0


def command_models_select(args: argparse.Namespace) -> int:
    print(json.dumps(select_model(args.model_id, args.artifact_root), indent=2)); return 0


def command_models_delete(args: argparse.Namespace) -> int:
    _require_force(args.force, f"delete model {args.model_id!r}")
    delete_model(args.model_id, args.artifact_root); print(f"Deleted factory model: {args.model_id}"); return 0


def command_train(args: argparse.Namespace) -> int:
    if args.replace:
        _require_force(args.force, f"replace model {args.model_id!r}")
    registered = get_factory(args.factory_id, args.registry) if args.factory_id else None
    print(train_factory_model(model_id=args.model_id, factory_json=registered["factory_json"] if registered else args.factory,
        runs_root=args.runs, configured_stations=registered.get("configured_stations") if registered else None,
        root=args.artifact_root, seed=args.seed, threshold_objective=args.threshold_objective,
        replace=args.replace, progress=print)); return 0


def command_factories_list(args: argparse.Namespace) -> int:
    print(json.dumps(list_factories(args.registry), indent=2)); return 0


def command_factories_show(args: argparse.Namespace) -> int:
    print(json.dumps(get_factory(args.factory_id, args.registry), indent=2)); return 0


def command_factory_add(args: argparse.Namespace) -> int:
    factory = Path(args.factory).expanduser().resolve()
    inferred = factory.parent.name if factory.stem.lower() == "factory" else factory.stem
    print(json.dumps(register_factory(args.factory_id or inferred, factory, args.registry, replace=args.replace), indent=2)); return 0


def command_factories_configure(args: argparse.Namespace) -> int:
    entry = get_factory(args.factory_id, args.registry)
    output = args.output or (Path(args.registry).expanduser().resolve().parent / "configurations" / entry["id"] / "configured_stations.csv")
    print(json.dumps(set_configured_stations(entry["id"], configure_factory(entry["factory_json"], args.stations, output), args.registry), indent=2)); return 0


def command_factories_delete(args: argparse.Namespace) -> int:
    _require_force(args.force, f"delete factory registration {args.factory_id!r}")
    delete_factory(args.factory_id, args.registry); print(f"Deleted factory registration: {args.factory_id}"); return 0


def command_run_prescribed(args: argparse.Namespace) -> int:
    from main import main as bottleneck_main
    run, output = _run_directory(args.run_dir), Path(args.output).expanduser().resolve()
    if output.exists():
        _require_force(args.force, f"overwrite prediction output {str(output)!r}")
    argv = ["replay", "--run-dir", str(run), "--output-jsonl", str(output), "--run-id", args.run_id or run.name]
    argv += _model_runtime_args(args.model_id, args.artifact_root)
    if not args.unpaced:
        argv += ["--pace", "--mult", str(args.mult)]
    return bottleneck_main(argv)


def command_run_random(args: argparse.Namespace) -> int:
    generated, runs = Path(args.generated).expanduser().resolve(), Path(args.runs).expanduser().resolve()
    if generated.exists() and any(generated.iterdir()):
        raise FileExistsError(f"Random-test generated-input directory already contains files: {generated}. Choose a new --generated directory.")
    if (runs / "run_0001").exists():
        raise FileExistsError(f"Random-test destination already exists: {runs / 'run_0001'}. Choose a new --runs directory.")
    print("Preparing a random simulator run...")
    generate(args.factory, generated, 1, args.seed, args.duration_ms, progress=print)
    run_generated(args.simulator, args.factory, generated, runs, fail_fast=True, progress=print)
    return command_run_prescribed(argparse.Namespace(run_dir=runs / "run_0001", output=args.output, run_id="random_run_0001",
        model_id=args.model_id, artifact_root=args.artifact_root, mult=args.mult, unpaced=args.unpaced, force=args.force))


def command_status(args: argparse.Namespace) -> int:
    print(json.dumps({"selected_model": selected_model_id(args.artifact_root), "models": list_models(args.artifact_root),
        "factories": list_factories(args.registry), "default_factory": str(DEFAULT_FACTORY), "default_runs": str(DEFAULT_RUNS),
        "simulator": str(DEFAULT_SIMULATOR)}, indent=2)); return 0


def _add_factory_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    factory = sub.add_parser("factory", help="Add, inspect, configure, and remove factory definitions")
    children = factory.add_subparsers(dest="factory_command", required=True)
    add = children.add_parser("add", help="Validate and register a factory.json")
    add.add_argument("factory", type=Path); add.add_argument("--id", dest="factory_id"); add.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY); add.add_argument("--replace", action="store_true"); add.set_defaults(func=command_factory_add)
    listing = children.add_parser("list", help="List registered factories")
    listing.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY); listing.set_defaults(func=command_factories_list)
    inspect = children.add_parser("inspect", help="Inspect a registered factory")
    inspect.add_argument("factory_id"); inspect.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY); inspect.set_defaults(func=command_factories_show)
    configure = children.add_parser("configure", help="Create and retain station configuration")
    configure.add_argument("factory_id"); configure.add_argument("--stations", type=Path, required=True); configure.add_argument("--output", type=Path); configure.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY); configure.set_defaults(func=command_factories_configure)
    remove = children.add_parser("remove", help="Remove a registration without deleting factory files")
    remove.add_argument("factory_id"); remove.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY); remove.add_argument("--force", action="store_true"); remove.set_defaults(func=command_factories_delete)


def _add_legacy_factories_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Retain the older plural spelling used by existing scripts and docs."""
    factories = sub.add_parser("factories", help="Compatibility alias for factory management")
    children = factories.add_subparsers(dest="factories_command", required=True)
    listing = children.add_parser("list"); listing.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY); listing.set_defaults(func=command_factories_list)
    show = children.add_parser("show"); show.add_argument("factory_id"); show.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY); show.set_defaults(func=command_factories_show)
    register = children.add_parser("register")
    register.add_argument("factory_id"); register.add_argument("factory", type=Path); register.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY); register.add_argument("--replace", action="store_true")
    register.set_defaults(func=lambda args: (print(json.dumps(register_factory(args.factory_id, args.factory, args.registry, replace=args.replace), indent=2)) or 0))
    configure = children.add_parser("configure")
    configure.add_argument("factory_id"); configure.add_argument("--stations", type=Path, required=True); configure.add_argument("--output", type=Path); configure.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY); configure.set_defaults(func=command_factories_configure)
    delete = children.add_parser("delete")
    delete.add_argument("factory_id"); delete.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY); delete.add_argument("--force", action="store_true"); delete.set_defaults(func=command_factories_delete)


def _add_generation_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--factory", type=Path, default=DEFAULT_FACTORY); command.add_argument("--output", type=Path, default=DEFAULT_GENERATED)
    command.add_argument("--count", type=int, required=True); command.add_argument("--seed", type=int, default=42); command.add_argument("--duration-ms", type=int, default=28_800_000)


def _add_simulation_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--simulator", type=Path, default=DEFAULT_SIMULATOR); command.add_argument("--factory", type=Path, default=DEFAULT_FACTORY)
    command.add_argument("--generated", type=Path, default=DEFAULT_GENERATED); command.add_argument("--output", type=Path, default=DEFAULT_RUNS); command.add_argument("--fail-fast", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Digital Twin project control shell")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_factory_commands(sub)
    _add_legacy_factories_commands(sub)
    configure = sub.add_parser("configure", help="Create a factory-specific station configuration")
    configure.add_argument("--factory", type=Path, default=DEFAULT_FACTORY); configure.add_argument("--stations", type=Path, required=True); configure.add_argument("--output", type=Path, required=True); configure.set_defaults(func=command_configure)
    generate_parser = sub.add_parser("generate", help="Generate factory-specific random scenarios"); _add_generation_options(generate_parser); generate_parser.set_defaults(func=command_generate)
    simulate = sub.add_parser("simulate", help="Run generated scenarios through the C++ simulator"); _add_simulation_options(simulate); simulate.set_defaults(func=command_simulate)
    data = sub.add_parser("data", help="Generate, run, inspect, or remove simulator runs")
    data_sub = data.add_subparsers(dest="data_command", required=True)
    data_list = data_sub.add_parser("list"); data_list.add_argument("--runs", type=Path, default=DEFAULT_RUNS); data_list.set_defaults(func=command_data_list)
    data_delete = data_sub.add_parser("delete"); data_delete.add_argument("run_id"); data_delete.add_argument("--runs", type=Path, default=DEFAULT_RUNS); data_delete.add_argument("--force", action="store_true"); data_delete.set_defaults(func=command_data_delete)
    data_generate = data_sub.add_parser("generate"); _add_generation_options(data_generate); data_generate.set_defaults(func=command_generate)
    data_simulate = data_sub.add_parser("simulate"); _add_simulation_options(data_simulate); data_simulate.set_defaults(func=command_simulate)
    models = sub.add_parser("models", help="Inspect, select, or delete model artifacts")
    models_sub = models.add_subparsers(dest="models_command", required=True)
    for name, func in (("list", command_models_list), ("select", command_models_select), ("use", command_models_select), ("delete", command_models_delete)):
        command = models_sub.add_parser(name); command.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
        if name != "list": command.add_argument("model_id")
        if name == "delete": command.add_argument("--force", action="store_true")
        command.set_defaults(func=func)
    train = sub.add_parser("train", help="Train and publish one immutable factory model artifact")
    train.add_argument("model_id"); train.add_argument("--factory", type=Path, default=DEFAULT_FACTORY); train.add_argument("--factory-id"); train.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY); train.add_argument("--runs", type=Path, default=DEFAULT_RUNS); train.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT); train.add_argument("--seed", type=int, default=42); train.add_argument("--threshold-objective", choices=("f1", "f2"), default="f2"); train.add_argument("--replace", action="store_true"); train.add_argument("--force", action="store_true"); train.set_defaults(func=command_train)
    run = sub.add_parser("run", help="Run a trained factory model against prescribed or random data")
    run_sub = run.add_subparsers(dest="run_command", required=True)
    prescribed = run_sub.add_parser("prescribed", help="Causally replay an existing completed run")
    prescribed.add_argument("--run-dir", type=Path, required=True); prescribed.add_argument("--output", type=Path, required=True); prescribed.add_argument("--run-id"); prescribed.add_argument("--model-id"); prescribed.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT); prescribed.add_argument("--mult", type=float, default=60.0); prescribed.add_argument("--unpaced", action="store_true"); prescribed.add_argument("--force", action="store_true"); prescribed.set_defaults(func=command_run_prescribed)
    random_run = run_sub.add_parser("random", help="Generate, simulate, then causally replay a new run")
    random_run.add_argument("--factory", type=Path, default=DEFAULT_FACTORY); random_run.add_argument("--simulator", type=Path, default=DEFAULT_SIMULATOR); random_run.add_argument("--generated", type=Path, default=DEFAULT_GENERATED / "random_test"); random_run.add_argument("--runs", type=Path, default=DEFAULT_RUNS / "random_test"); random_run.add_argument("--output", type=Path, required=True); random_run.add_argument("--model-id"); random_run.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT); random_run.add_argument("--seed", type=int, default=42); random_run.add_argument("--duration-ms", type=int, default=28_800_000); random_run.add_argument("--mult", type=float, default=60.0); random_run.add_argument("--unpaced", action="store_true"); random_run.add_argument("--force", action="store_true"); random_run.set_defaults(func=command_run_random)
    status = sub.add_parser("status", help="Show selected model and default paths")
    status.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT); status.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY); status.set_defaults(func=command_status)
    shell = sub.add_parser("shell", help="Start the interactive cross-platform Python shell"); shell.set_defaults(func=lambda _: interactive_shell(parser))
    return parser


def interactive_shell(parser: argparse.ArgumentParser) -> int:
    print("Digital Twin Shell. Type 'help' for commands, or 'exit' to leave.")
    while True:
        try:
            line = input("dt> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return 0
        if not line:
            continue
        if line.lower() in {"quit", "exit"}:
            return 0
        if line.lower() in {"help", "?"}:
            parser.print_help(); continue
        try:
            args = parser.parse_args(shlex.split(line))
            if args.command == "shell": print("Already in the interactive shell.")
            else: args.func(args)
        except SystemExit:
            continue
        except Exception as error:
            print(f"ERROR: {error}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is not None and not argv: return interactive_shell(parser)
    if argv is None and len(sys.argv) == 1: return interactive_shell(parser)
    args = parser.parse_args(argv)
    try: return int(args.func(args))
    except Exception as error: parser.exit(2, f"ERROR: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
