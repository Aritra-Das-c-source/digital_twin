# Final Bottleneck Prediction Pipeline

## Production workflow

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

Install dependencies (Python 3.10+ recommended), then run the launcher:

```bash
pip install -r requirements.txt
python run_current.py
```

Run it from the `bottlenecks_prediction/` directory so the relative `data/`, `config/`, and `dark_zone/` paths resolve correctly.

By default the launcher prints the available station IDs and prompts you to enter which ones are DARK (comma-separated, e.g. `S08,S12,S13,S14`; leave blank for all LIGHT). You can skip the prompt with flags:

```bash
python run_current.py --dark-stations S08,S12,S13,S14 --particles 3000 --run-id CURRENT_RUN
```

| Flag | Default | Description |
|---|---|---|
| `--dark-stations` | prompts interactively | Comma-separated DARK station IDs |
| `--particles` | `3000` | Number of simulation particles |
| `--output` | `data/output/predictions.jsonl` | Where predictions are written |
| `--run-id` | `CURRENT_RUN` | Label for this run |

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
