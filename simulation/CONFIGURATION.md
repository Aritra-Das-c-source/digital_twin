# Simulation configuration

`factory.json` is the fixed physical line: contiguous station IDs, processing and buffer parameters, sensor coverage, and checkpoints. A scenario supplies a seed, duration, dynamic degradation and optional dark zones. `defects.json` defines defect introduction and downstream effects.

Run the default mixed example from a build directory with `simulation`, or choose files explicitly:

`simulation --factory config/factory.json --scenario config/scenarios/defects.json --defects config/defects.json --output output/defects_run`

A dark zone is an internal inclusive station range. Normal unit movement events for its stations are suppressed. `station_events.csv` instead records `DARK_ZONE_ENTERED` and `DARK_ZONE_EXITED` with a `dark_zone_id` and unit identity. Sensors remain station-level and never contain a unit ID. Manual checks and non-identifying checkpoints inside zones leave `unit_id` blank; an identity-capable checkpoint emits a sparse identified read when reliable.

Defects live on individual units. Introduction probability is `baseProbability + degradationLevel * degradationSensitivity`, clamped to one. Cycle effects multiply after the normal degradation multiplier; CV additions accumulate. Sensor shifts add to normal degradation/activity signals. Multiple defect manual-check effects use the maximum configured failure probability. Inspections independently attempt each applicable defect and report only detected defects.
