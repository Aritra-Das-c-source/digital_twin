# Final Bottleneck Prediction Pipeline

## Factory-specific training and operations

The canonical training input is the simulator's completed run directory tree:

```text
simulation/training/runs/run_0001/
simulation/training/runs/run_0002/
...
```

Use the internal shell from the repository root:

```powershell
python bottlenecks_prediction/cli.py generate --count 20
python bottlenecks_prediction/cli.py simulate
python bottlenecks_prediction/cli.py train factory-a
python bottlenecks_prediction/cli.py models list
python bottlenecks_prediction/cli.py models select factory-a
python bottlenecks_prediction/cli.py run prescribed --run-dir simulation/training/runs/run_0001 --output predictions.jsonl --unpaced
```

`train` reads those run folders in place, builds only the frozen 28-feature
bottleneck dataset, starts from the protected initial/base XGBoost state, and
publishes `factory_models/<factory-id>/`. Each artifact has its own bundle,
feature contract/category levels/threshold, configured-stations topology, and
DARK historical calibration. It deliberately excludes runtime queues, PF state,
recent observations, output predictions, and raw CSV copies.

The selected model is only a pointer in `factory_models/selected_model.json`.
Selecting a different artifact never modifies either model. The `base` model is
protected; deleting a run or factory artifact requires `--force` and the shell
will never delete `base`.

`run prescribed` uses only the selected artifact's historical calibration, so
the evaluated run cannot calibrate or train itself. By default it paces event
delivery at `--mult` (60× by default); use `--unpaced` for a fast offline replay.
The runtime still receives events in timestamp order through the same
`process_event()` / `advance_time()` implementation used for live input.

## Legacy current-run workflow

The simulator writes a **completed run** into:

```text
data/input/current_run/
├── stations.csv
├── units.csv
├── station_events.csv
└── manual_checks.csv       # optional
```

Factory checkpoint layout is kept separately at:

```text
config/station_checkpoints.csv
```

Prior completed runs used only for causal DARK calibration live under:

```text
data/calibration/history/<run_name>/
├── stations.csv
├── units.csv
└── station_events.csv
```

The current run is **never** used to calibrate itself by `run_current.py`.

## One-click use
run it from run_current.py

## Simulator contract

Required runtime files and columns:

- `stations.csv`: must contain `station_id` and the existing station configuration columns used by the project.
- `units.csv`: must contain `unit_id`, `vehicle_model`.
- `station_events.csv`: must contain `timestamp_ms`, `station_id`, `unit_id`, `event_type`.

Do not change existing column names/schema. The simulator should finish writing the run before the prediction launcher is started.

`manual_checks.csv` is optional. `sensor_readings.csv` and `inspection_results.csv` are not required by the final prediction runtime.

## Causality

For DARK stations, `run_current.py` builds `historical_dwell.csv` and corridor-residence calibration only from prior completed runs under `data/calibration/history/`. The current run is excluded from calibration. Runtime features then use only events/evidence available up to each prediction timestamp.

## Tests

```bash
python -m pytest tests -q
```

Current packaged suite: **13 tests**.

## Performance note

The model/runtime correctness path is validated. Optimization of very large accelerated replays at the production 3000-particle setting remains a separate performance task.
