from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from dashboard.storage.schema import INITIAL_SCHEMA

MIGRATIONS: dict[int, list[str]] = {
    1: INITIAL_SCHEMA
}

def get_current_version(conn: sqlite3.Connection) -> int | None:
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_versions'"
        )
        if not cursor.fetchone():
            return None
            
        cursor = conn.execute("SELECT MAX(version) FROM schema_versions")
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else None
    except sqlite3.Error:
        return None

def apply_migrations(conn: sqlite3.Connection, current_version: int | None) -> int:
    start_version = 0 if current_version is None else current_version
    max_version = max(MIGRATIONS.keys())
    
    if start_version >= max_version:
        return start_version

    for version in range(start_version + 1, max_version + 1):
        if version in MIGRATIONS:
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
            
            conn.execute(
                "INSERT INTO schema_versions (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat())
            )
            
    conn.commit()
    return max_version
