# Digital Twin

Digital Twin is a factory-specific simulation, bottleneck-prediction, and
runtime-replay project. The repository-root [cli.py](cli.py) is its control
plane; simulation, training, DARK inference, and live runtime remain separate
components behind it.

## Setup

Supported platforms are Windows, Linux, and macOS. Use Python 3.11+ and install
the bottleneck dependencies:

```text
# Windows
py -3.11 -m pip install -r bottlenecks_prediction/requirements.txt

# Linux/macOS
python3 -m pip install -r bottlenecks_prediction/requirements.txt
```

On Windows, use `py cli.py` when `python` is not the selected launcher. If
`py -0p` reports no interpreter, install/register Python or invoke the known
interpreter directly. The CLI itself is platform-neutral and does not require
PowerShell, Bash, or CMD.

## Shell and one-shot commands

Start the interactive shell:

```text
py cli.py
dt> factory add simulation/config/factory.json --id demo-factory
dt> factory list
dt> data generate --count 20
dt> data simulate
dt> train demo-factory --factory-id demo-factory
dt> models use demo-factory
dt> exit
```

The same parser and handlers serve automation:

```text
py cli.py factory add simulation/config/factory.json --id demo-factory
py cli.py factory configure demo-factory --stations simulation/training/runs/run_0001/stations.csv
py cli.py data generate --count 20 --seed 2026
py cli.py data simulate
py cli.py train demo-factory --factory-id demo-factory
py cli.py models list
py cli.py models use demo-factory
```

`factory add` validates and registers `factory.json` in
`.digital_twin/factories.json`; `factory inspect`, `factory list`, and
`factory remove --force` manage that registry. Removing a registration never
deletes the factory JSON or its configured-stations file.

The `C:/Projects/factories/...` form is only an example of a user-owned
factory location: it must be replaced with a JSON file that exists on your
machine. The commands above use the repository's bundled factory definition.

## Workflow

```text
factory.json ──> factory registry/configuration
                         │
                         ├─> scenario generation ─> C++ simulator ─> completed run_* CSVs
                         │                                      │
                         └─> bottleneck feature training <──────┘
                                      │
                      DARK zones? ──┼── yes: causal historical calibration
                                    └── no: skip calibration
                                      │
                    immutable factory model artifact ─> selected-model pointer ─> causal runtime replay/live input
```

The 28-feature bottleneck model is shared by all factories. Every training
operation begins from the protected base model, writes a separate artifact, and
never loads another factory's learned weights. `base` cannot be deleted;
`models use <id>` only switches the small selection pointer and does not
retrain.

For a factory with DARK zones, training creates historical dwell and (when
needed) corridor-residence calibration from completed training runs only. For a
factory with no DARK zones, calibration is explicitly absent and no DARK code
is run. Runtime reads the artifact's configured station topology, so LIGHT-only
artifacts do not require DARK files.

## Running a trained model

First select the artifact to use. This is instant; it does not train or copy
the model:

```text
# Windows
py cli.py models use factory-a

# Linux/macOS
python3 cli.py models use factory-a
```

There are two post-training run modes.

### Prescribed replay

Use this when a completed simulator run already exists and you want predictions
for that exact event sequence. The run directory must contain `stations.csv`,
`units.csv`, and `station_events.csv`.

```text
py cli.py run prescribed --run-dir simulation/training/runs/run_0001 --output predictions/factory-a-run-0001.jsonl --unpaced
```

In the interactive shell, omit `py cli.py` and enter the same command after
`dt>`. `--unpaced` processes the timestamp-ordered sequence as fast as
possible, which is normally right for offline analysis. Leave it off to pace
causal delivery at `--mult 60` (60x), or choose another multiplier. Add
`--model-id factory-a` to use an artifact without changing the selected model.
Existing prediction files require `--force`.

### Random end-to-end run

Use this to create a new random scenario, execute the C++ simulator, and
replay it with the selected model in one command. Choose fresh directories for
generated inputs and simulator output; the command deliberately refuses to
overwrite completed data.

```text
py cli.py run random --factory simulation/config/factory.json --generated simulation/training/generated/random-factory-a --runs simulation/training/runs/random-factory-a --output predictions/factory-a-random.jsonl --seed 2026 --unpaced
```

The random command shows generation and simulator progress, then writes the
same JSONL prediction format as prescribed replay. It accepts `--model-id`,
`--mult`, and `--force` with the same meanings. Use `run prescribed` for a
known scenario; use `run random` for a newly generated end-to-end test.

## Data, simulation, and runtime

`data generate` creates reproducible scenarios; `data simulate` invokes the
C++ simulator with executable arguments (not a shell command) and creates
independent `run_*` folders. `data list` and `data delete --force` inspect and
remove completed runs.

`run prescribed` causally replays a completed run; `run random` generates,
simulates, then replays one run. With pacing enabled, `--mult` is both the
simulation-time-to-wall-clock delivery multiplier and the event-delivery speed;
`--unpaced` processes the causal event sequence as fast as possible. Events
remain timestamp ordered in either mode.

Useful regression tests remain under `bottlenecks_prediction/tests`. Diagnostics
such as training metrics are retained in the artifact manifest as metadata but
are not runtime state.
