"""Schema version 2 -- the analytical read model.

Version 1 gave the dashboard a run registry: one row per production run, with every
interesting number buried in a ``metadata_json`` blob. That is enough to list runs and
nothing else, so every view that needed a number re-parsed the prediction JSONL on each
Streamlit rerun (up to 71 MB per file in this repository).

Version 2 keeps version 1's ``runs`` table and adds a normalized projection of the
artifacts around it. The principle from ``dashboard/storage/schema.py`` is unchanged and
is what makes this safe: **the artifacts on disk stay authoritative and this database
stays disposable.** Every table here is derived and rebuildable, and
``dashboard.ingestion`` can reconstruct all of it from the same run directories the
existing pipeline already wrote.

Design rules encoded below
--------------------------

* **The two prediction streams stay separate.** ``bottleneck_predictions`` is a
  station/flow signal, ``defect_predictions`` is a vehicle-quality signal. There is no
  join table and no one-to-one relationship between them, per
  ``DASHBOARD_CONTRACTS.md`` section 2.
* **Identity is always (run, entity, simulator time).** Every fact table is keyed by
  ``run_id`` first. The same ``S12`` or ``U000001`` appears in every run and must never
  bleed across them.
* **Ingestion is idempotent by construction.** Fact rows carry ``source_seq``, the
  0-based ordinal of the record inside its append-only source artifact. ``(run_id,
  source_seq)`` is the primary key, so re-reading the same artifact writes the same rows
  into the same slots.
* **Topology is factory-scoped data, not UI code.** ``stations`` and ``dark_zones`` hang
  off a factory fingerprint, so a run always renders against the topology it actually ran
  on even after ``factory.json`` is edited afterwards.
* **Nothing is invented.** Tables exist only where the repository already produces the
  data. ``prediction_outcomes`` and ``model_metrics`` are created empty: the structure is
  needed now so later work can evaluate predictions against observed outcomes, but no row
  is written until a real outcome is observed.
"""

from __future__ import annotations

# -- topology -------------------------------------------------------------------------

CREATE_FACTORIES_TABLE = """
CREATE TABLE IF NOT EXISTS factories (
    fingerprint     TEXT    PRIMARY KEY,
    path            TEXT    NOT NULL,
    name            TEXT,
    station_count   INTEGER NOT NULL DEFAULT 0,
    dark_zone_count INTEGER NOT NULL DEFAULT 0,
    is_demo         INTEGER NOT NULL DEFAULT 0,
    topology_json   TEXT,
    first_seen_at   TEXT    NOT NULL
)
"""

# One row per station of one factory revision. `station_id` is the *runtime* label
# (`S12`), because that is the identifier every artifact and both prediction streams use.
# `station_index` is the factory.json id, which is also the line sequence: the simulator
# labels station id N as S(N+1).
CREATE_STATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS stations (
    factory_fingerprint   TEXT    NOT NULL,
    station_id            TEXT    NOT NULL,
    station_index         INTEGER NOT NULL,
    name                  TEXT    NOT NULL,
    archetype             TEXT    NOT NULL DEFAULT 'AUTOMATED',
    mean_cycle_time_ms    INTEGER NOT NULL DEFAULT 0,
    cycle_time_cv         REAL    NOT NULL DEFAULT 0,
    buffer_capacity       INTEGER NOT NULL DEFAULT 0,
    sensor_coverage       TEXT    NOT NULL DEFAULT 'NONE',
    is_source             INTEGER NOT NULL DEFAULT 0,
    is_sink               INTEGER NOT NULL DEFAULT 0,
    is_dark               INTEGER NOT NULL DEFAULT 0,
    dark_zone_id          TEXT,
    upstream_station_id   TEXT,
    downstream_station_id TEXT,
    PRIMARY KEY (factory_fingerprint, station_id)
)
"""

# DARK membership comes from the factory's darkZones contract (dz.csv at runtime), never
# from sensorCoverage -- see DASHBOARD_CONTRACTS.md section 5.
CREATE_DARK_ZONES_TABLE = """
CREATE TABLE IF NOT EXISTS dark_zones (
    factory_fingerprint TEXT    NOT NULL,
    dark_zone_id        TEXT    NOT NULL,
    name                TEXT,
    start_station_id    TEXT    NOT NULL,
    end_station_id      TEXT    NOT NULL,
    sensor_telemetry    INTEGER NOT NULL DEFAULT 0,
    manual_checks       INTEGER NOT NULL DEFAULT 0,
    checkpoints         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (factory_fingerprint, dark_zone_id)
)
"""

# -- per-run entities -------------------------------------------------------------------

CREATE_UNITS_TABLE = """
CREATE TABLE IF NOT EXISTS units (
    run_id            TEXT    NOT NULL,
    unit_id           TEXT    NOT NULL,
    created_at_ms     INTEGER,
    vehicle_model     TEXT,
    supplier_batch    TEXT,
    first_seen_ms     INTEGER,
    last_seen_ms      INTEGER,
    last_station_id   TEXT,
    completed         INTEGER NOT NULL DEFAULT 0,
    inspection_result TEXT,
    PRIMARY KEY (run_id, unit_id)
)
"""

# -- prediction streams (kept separate, never joined one-to-one) -------------------------

CREATE_BOTTLENECK_PREDICTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS bottleneck_predictions (
    run_id             TEXT    NOT NULL,
    source_seq         INTEGER NOT NULL,
    timestamp_ms       INTEGER NOT NULL,
    station_id         TEXT    NOT NULL,
    vehicle_id         TEXT,
    zone               TEXT,
    route              TEXT,
    prediction_trigger TEXT,
    probability        REAL,
    risk_percent       REAL,
    warning            INTEGER NOT NULL DEFAULT 0,
    decision_threshold REAL,
    threshold_crossed  INTEGER NOT NULL DEFAULT 0,
    state_confidence   REAL,
    event_id           TEXT,
    event_sequence     INTEGER,
    model_id           TEXT,
    schema_version     TEXT,
    drivers_json       TEXT,
    dark_state_json    TEXT,
    PRIMARY KEY (run_id, source_seq)
)
"""

# `warning` and `threshold_crossed` are copied from the record, never recomputed from
# probability and threshold: DASHBOARD_CONTRACTS.md section 4 documents that a defect
# `warning` is deliberately suppressed at the final inspection station, so a row can
# legally carry threshold_crossed = 1 with warning = 0.
CREATE_DEFECT_PREDICTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS defect_predictions (
    run_id                   TEXT    NOT NULL,
    source_seq               INTEGER NOT NULL,
    timestamp_ms             INTEGER NOT NULL,
    unit_id                  TEXT    NOT NULL,
    station_id               TEXT,
    station_index            INTEGER,
    final_inspection_station TEXT,
    route                    TEXT,
    prediction_trigger       TEXT,
    data_source              TEXT,
    probability              REAL,
    risk_percent             REAL,
    raw_probability          REAL,
    alert_policy             TEXT,
    alert_policy_score       REAL,
    decision_threshold       REAL,
    threshold_crossed        INTEGER NOT NULL DEFAULT 0,
    warning                  INTEGER NOT NULL DEFAULT 0,
    state_confidence         REAL,
    model_id                 TEXT,
    schema_version           TEXT,
    risk_drivers_json        TEXT,
    protective_drivers_json  TEXT,
    PRIMARY KEY (run_id, source_seq)
)
"""

# -- observed line state -----------------------------------------------------------------

# The projection of station_events.csv. This is the only place queue occupancy exists:
# it is in neither prediction stream, which is why the Live Factory needs it.
# `occupancy` is the simulator's `queue_length_after`.
CREATE_QUEUE_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS queue_snapshots (
    run_id        TEXT    NOT NULL,
    source_seq    INTEGER NOT NULL,
    timestamp_ms  INTEGER NOT NULL,
    station_id    TEXT    NOT NULL,
    event_type    TEXT    NOT NULL,
    unit_id       TEXT,
    occupancy     INTEGER,
    cycle_time_ms INTEGER,
    dark_zone_id  TEXT,
    PRIMARY KEY (run_id, source_seq)
)
"""

# Aggregated evidence per station and channel. Raw sensor_readings.csv is ~234k rows per
# run and carries no information the dashboard needs beyond "which channels were actually
# observed here, how often, over what span" -- so it is summarised, not copied.
CREATE_SENSOR_OBSERVATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS sensor_observations (
    run_id             TEXT    NOT NULL,
    station_id         TEXT    NOT NULL,
    channel_kind       TEXT    NOT NULL,
    channel            TEXT    NOT NULL,
    observation_count  INTEGER NOT NULL DEFAULT 0,
    first_timestamp_ms INTEGER,
    last_timestamp_ms  INTEGER,
    PRIMARY KEY (run_id, station_id, channel_kind, channel)
)
"""

# -- derived analytics --------------------------------------------------------------------

CREATE_ALERTS_TABLE = """
CREATE TABLE IF NOT EXISTS alerts (
    alert_id            TEXT    PRIMARY KEY,
    run_id              TEXT    NOT NULL,
    alert_type          TEXT    NOT NULL,
    entity_type         TEXT    NOT NULL,
    entity_id           TEXT    NOT NULL,
    station_id          TEXT,
    started_at_ms       INTEGER NOT NULL,
    ended_at_ms         INTEGER,
    duration_ms         INTEGER,
    severity            TEXT    NOT NULL,
    status              TEXT    NOT NULL,
    opening_probability REAL,
    peak_probability    REAL,
    closing_probability REAL,
    decision_threshold  REAL,
    min_confidence      REAL,
    mean_confidence     REAL,
    observation_count   INTEGER NOT NULL DEFAULT 0,
    top_driver          TEXT
)
"""

CREATE_RUN_METRICS_TABLE = """
CREATE TABLE IF NOT EXISTS run_metrics (
    run_id                      TEXT    PRIMARY KEY,
    simulated_duration_ms       INTEGER,
    first_timestamp_ms          INTEGER,
    last_timestamp_ms           INTEGER,
    units_created               INTEGER NOT NULL DEFAULT 0,
    units_completed             INTEGER NOT NULL DEFAULT 0,
    throughput_per_hour         REAL,
    avg_lead_time_ms            REAL,
    p95_lead_time_ms            REAL,
    avg_wip                     REAL,
    peak_wip                    INTEGER,
    bottleneck_prediction_count INTEGER NOT NULL DEFAULT 0,
    defect_prediction_count     INTEGER NOT NULL DEFAULT 0,
    bottleneck_alert_count      INTEGER NOT NULL DEFAULT 0,
    defect_alert_count          INTEGER NOT NULL DEFAULT 0,
    critical_alert_count        INTEGER NOT NULL DEFAULT 0,
    station_count               INTEGER NOT NULL DEFAULT 0,
    observed_station_count      INTEGER NOT NULL DEFAULT 0,
    predicted_station_count     INTEGER NOT NULL DEFAULT 0,
    dark_station_count          INTEGER NOT NULL DEFAULT 0,
    observability_coverage      REAL,
    mean_state_confidence       REAL,
    health_status               TEXT,
    computed_at                 TEXT    NOT NULL
)
"""

CREATE_STATION_METRICS_TABLE = """
CREATE TABLE IF NOT EXISTS station_metrics (
    run_id                  TEXT    NOT NULL,
    station_id              TEXT    NOT NULL,
    station_index           INTEGER,
    prediction_count        INTEGER NOT NULL DEFAULT 0,
    last_probability        REAL,
    avg_probability         REAL,
    peak_probability        REAL,
    decision_threshold      REAL,
    time_above_threshold_ms INTEGER NOT NULL DEFAULT 0,
    observed_span_ms        INTEGER NOT NULL DEFAULT 0,
    warning_count           INTEGER NOT NULL DEFAULT 0,
    critical_count          INTEGER NOT NULL DEFAULT 0,
    alert_count             INTEGER NOT NULL DEFAULT 0,
    mean_confidence         REAL,
    min_confidence          REAL,
    avg_queue               REAL,
    peak_queue              INTEGER,
    last_queue              INTEGER,
    buffer_capacity         INTEGER,
    utilization             REAL,
    busy_ms                 INTEGER NOT NULL DEFAULT 0,
    units_processed         INTEGER NOT NULL DEFAULT 0,
    observability           TEXT,
    computed_at             TEXT    NOT NULL,
    PRIMARY KEY (run_id, station_id)
)
"""

CREATE_UNIT_METRICS_TABLE = """
CREATE TABLE IF NOT EXISTS unit_metrics (
    run_id                      TEXT    NOT NULL,
    unit_id                     TEXT    NOT NULL,
    prediction_count            INTEGER NOT NULL DEFAULT 0,
    last_probability            REAL,
    avg_probability             REAL,
    peak_probability            REAL,
    peak_at_station_id          TEXT,
    decision_threshold          REAL,
    warning_count               INTEGER NOT NULL DEFAULT 0,
    first_warning_ms            INTEGER,
    mean_confidence             REAL,
    last_station_id             TEXT,
    last_timestamp_ms           INTEGER,
    largest_increase            REAL,
    largest_increase_station_id TEXT,
    lead_time_ms                INTEGER,
    inspection_result           TEXT,
    computed_at                 TEXT    NOT NULL,
    PRIMARY KEY (run_id, unit_id)
)
"""

# Created empty on purpose. A row appears only once a real observed outcome exists for a
# prediction; nothing here is ever filled in from probabilities alone.
CREATE_PREDICTION_OUTCOMES_TABLE = """
CREATE TABLE IF NOT EXISTS prediction_outcomes (
    outcome_id         TEXT    PRIMARY KEY,
    run_id             TEXT    NOT NULL,
    prediction_kind    TEXT    NOT NULL,
    subject_type       TEXT    NOT NULL,
    subject_id         TEXT    NOT NULL,
    station_id         TEXT,
    predicted_at_ms    INTEGER NOT NULL,
    predicted_positive INTEGER NOT NULL DEFAULT 0,
    probability        REAL,
    horizon_ms         INTEGER,
    outcome_time_ms    INTEGER,
    outcome_type       TEXT,
    actual_outcome     TEXT,
    outcome_source     TEXT,
    validation_status  TEXT    NOT NULL,
    lead_time_ms       INTEGER,
    computed_at        TEXT    NOT NULL
)
"""

CREATE_MODEL_METRICS_TABLE = """
CREATE TABLE IF NOT EXISTS model_metrics (
    run_id             TEXT    NOT NULL,
    model_kind         TEXT    NOT NULL,
    model_id           TEXT,
    decision_threshold REAL,
    evaluable_count    INTEGER NOT NULL DEFAULT 0,
    censored_count     INTEGER NOT NULL DEFAULT 0,
    true_positives     INTEGER,
    false_positives    INTEGER,
    true_negatives     INTEGER,
    false_negatives    INTEGER,
    precision_value    REAL,
    recall_value       REAL,
    false_alarm_rate   REAL,
    mean_lead_time_ms  REAL,
    method_version     TEXT,
    computed_at        TEXT    NOT NULL,
    PRIMARY KEY (run_id, model_kind)
)
"""

# What was ingested, from which artifact, in what state. The fingerprint lets a repeat
# ingest of an unchanged artifact short-circuit instead of re-reading 71 MB of JSONL.
CREATE_INGEST_CURSORS_TABLE = """
CREATE TABLE IF NOT EXISTS ingest_cursors (
    run_id           TEXT    NOT NULL,
    source           TEXT    NOT NULL,
    source_path      TEXT,
    source_size      INTEGER,
    source_mtime     REAL,
    fingerprint      TEXT,
    records_ingested INTEGER NOT NULL DEFAULT 0,
    malformed_lines  INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT    NOT NULL,
    PRIMARY KEY (run_id, source)
)
"""

# -- indexes ------------------------------------------------------------------------------
#
# Every access pattern the analytics layer uses is (run_id, entity, timestamp) or
# (run_id, timestamp). Leading with run_id is what keeps runs from contaminating one
# another even on a full scan.

INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_stations_factory_index ON stations (factory_fingerprint, station_index)",
    "CREATE INDEX IF NOT EXISTS idx_units_run_station ON units (run_id, last_station_id)",
    "CREATE INDEX IF NOT EXISTS idx_bp_run_station_time ON bottleneck_predictions (run_id, station_id, timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_bp_run_time ON bottleneck_predictions (run_id, timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_bp_run_vehicle_time ON bottleneck_predictions (run_id, vehicle_id, timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_bp_run_warning ON bottleneck_predictions (run_id, warning, timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_dp_run_unit_time ON defect_predictions (run_id, unit_id, timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_dp_run_time ON defect_predictions (run_id, timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_dp_run_station_time ON defect_predictions (run_id, station_id, timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_dp_run_warning ON defect_predictions (run_id, warning, timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_qs_run_station_time ON queue_snapshots (run_id, station_id, timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_qs_run_time ON queue_snapshots (run_id, timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_qs_run_unit_time ON queue_snapshots (run_id, unit_id, timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_sensor_run_station ON sensor_observations (run_id, station_id)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_run_entity ON alerts (run_id, entity_type, entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_run_type ON alerts (run_id, alert_type, severity)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_run_start ON alerts (run_id, started_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_station_metrics_run ON station_metrics (run_id, station_index)",
    "CREATE INDEX IF NOT EXISTS idx_unit_metrics_run_peak ON unit_metrics (run_id, peak_probability)",
    "CREATE INDEX IF NOT EXISTS idx_outcomes_run_subject ON prediction_outcomes (run_id, subject_type, subject_id)",
)

#: Columns added to the version 1 ``runs`` table. Applied with ALTER TABLE so an existing
#: version 1 database upgrades in place instead of being replaced.
RUNS_COLUMNS_V2: tuple[tuple[str, str], ...] = (
    ("analytics_state", "TEXT NOT NULL DEFAULT 'PENDING'"),
    ("analytics_ingested_at", "TEXT"),
    ("simulated_duration_ms", "INTEGER"),
)

ANALYTICS_SCHEMA: tuple[str, ...] = (
    CREATE_FACTORIES_TABLE,
    CREATE_STATIONS_TABLE,
    CREATE_DARK_ZONES_TABLE,
    CREATE_UNITS_TABLE,
    CREATE_BOTTLENECK_PREDICTIONS_TABLE,
    CREATE_DEFECT_PREDICTIONS_TABLE,
    CREATE_QUEUE_SNAPSHOTS_TABLE,
    CREATE_SENSOR_OBSERVATIONS_TABLE,
    CREATE_ALERTS_TABLE,
    CREATE_RUN_METRICS_TABLE,
    CREATE_STATION_METRICS_TABLE,
    CREATE_UNIT_METRICS_TABLE,
    CREATE_PREDICTION_OUTCOMES_TABLE,
    CREATE_MODEL_METRICS_TABLE,
    CREATE_INGEST_CURSORS_TABLE,
    *INDEXES,
)

#: Tables whose rows are scoped to one run. Clearing or re-ingesting a run deletes from
#: exactly these, which is what makes ingestion idempotent and history clearable.
RUN_SCOPED_TABLES: tuple[str, ...] = (
    "bottleneck_predictions",
    "defect_predictions",
    "queue_snapshots",
    "sensor_observations",
    "units",
    "alerts",
    "run_metrics",
    "station_metrics",
    "unit_metrics",
    "prediction_outcomes",
    "model_metrics",
    "ingest_cursors",
)

#: Everything the analytical read model owns, most dependent first. Used by Clear History.
ANALYTICS_TABLES: tuple[str, ...] = RUN_SCOPED_TABLES + ("stations", "dark_zones", "factories")
