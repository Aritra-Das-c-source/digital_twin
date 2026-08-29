# Training input generation and simulation orchestration

These tools are separate from the C++ simulator. They use only Python's standard library and
never archive configurations or modify simulator inputs.

`scenario_generator.py` reads the selected `factory.json`, including stations and DARK zones,
then produces paired scenario and defect files plus `manifest.json`. It rotates through healthy,
gradual, accelerating, step, intermittent, and severe operating conditions. Target stations,
sensor effects, manual checks, and downstream inspections are derived from the factory topology.
The generator seed makes the result reproducible.

`orchestrator.py` reads that manifest, invokes the simulator once per pair, records stdout/stderr
inside each run directory, and writes `run_manifest.json` with every return code. It continues
after failures by default; use `--fail-fast` to stop at the first failure.

For normal operations, the same generator and orchestrator are exposed through the thin project
shell, which defaults to the canonical locations below and does not copy the completed run CSVs:

```powershell
python bottlenecks_prediction/cli.py generate --count 20 --seed 2026
python bottlenecks_prediction/cli.py simulate
python bottlenecks_prediction/cli.py data list
python bottlenecks_prediction/cli.py train factory-a
```

The resulting `simulation/training/runs/run_*` folders are the direct bottleneck-training and
DARK-calibration source. Do not ZIP or move them into a separate `current_run` staging area for
factory training.

Generate five input pairs:

```powershell
python simulation/training/scenario_generator.py --factory simulation/config/factory.json --output training/generated --count 5 --seed 2026
```

Run existing generated inputs (replace the executable path with your build configuration):

```powershell
python simulation/training/orchestrator.py run --simulator simulation/build/Debug/simulation.exe --factory simulation/config/factory.json --generated training/generated --output training/runs
```

Generate and run in one command:

```powershell
python simulation/training/orchestrator.py all --simulator simulation/build/Debug/simulation.exe --factory simulation/config/factory.json --generated training/generated --output training/runs --count 20 --seed 2026
```
