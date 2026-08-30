from __future__ import annotations

SCHEMA_VERSION = 1

CREATE_SCHEMA_VERSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_versions (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

CREATE_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    production_day INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    scenario_name TEXT,
    scenario_description TEXT,
    multiplier REAL NOT NULL DEFAULT 60.0,
    factory_path TEXT NOT NULL,
    artifact_path TEXT,
    started_at TEXT,
    completed_at TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

INITIAL_SCHEMA = [CREATE_SCHEMA_VERSIONS_TABLE, CREATE_RUNS_TABLE]
