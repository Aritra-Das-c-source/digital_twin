# Simulation configuration

`factory.json` is the fixed physical line: contiguous station IDs, processing and buffer parameters, sensor coverage, and checkpoints. A scenario supplies a seed, duration, dynamic degradation and optional dark zones. `defects.json` defines defect introduction and downstream effects.

Sensor coverage determines the emitted telemetry: `PARTIAL` stations emit `VIBRATION` (g) and `TEMPERATURE` (C); `HIGH` stations also emit `CURRENT` (A) and `TORQUE` (Nm); `NONE` emits no telemetry. Defect `sensorEffects` may use any emitted sensor type, including `TORQUE`, for example `"sensorEffects":{"TORQUE":{"meanShift":4.0}}`.

For fast training-data generation, use the ZIP runtime interface. The configuration ZIP must
contain exactly one `factory.json`, exactly one `defects.json`, and a scenario as either
`scenario.json` or the sole JSON file in `scenarios/`. It produces a ZIP containing every CSV.
Choose the output ZIP filename with `--output`.

`simulation --config training_config.zip --output batch_042.zip`

This interface uses the built-in Windows PowerShell archive commands. Run the default mixed
example from a build directory with `simulation`, or choose files explicitly:

`simulation --factory config/factory.json --scenario config/scenarios/defects.json --defects config/defects.json --output output/defects_run`

A dark zone is an internal inclusive station range. Normal unit movement events for its stations are suppressed. `station_events.csv` instead records `DARK_ZONE_ENTERED` and `DARK_ZONE_EXITED` with a `dark_zone_id` and unit identity. Sensors remain station-level and never contain a unit ID. Manual checks and non-identifying checkpoints inside zones leave `unit_id` blank; an identity-capable checkpoint emits a sparse identified read when reliable.

Defects live on individual units. Introduction probability is `baseProbability + degradationLevel * degradationSensitivity`, clamped to one. Cycle effects multiply after the normal degradation multiplier; CV additions accumulate. Sensor shifts add to normal degradation/activity signals. Multiple defect manual-check effects use the maximum configured failure probability. Inspections independently attempt each applicable defect and report only detected defects.
