"""Forward-only schema migrations for the dashboard database.

Adding a schema change means appending a new integer key to :data:`MIGRATIONS` with the
steps that move the database from ``version - 1`` to ``version``. Existing entries are
never edited, so an older dashboard database upgrades cleanly.

A step is either a SQL string or a callable taking the open connection, which is what
lets version 2 add columns to the version 1 ``runs`` table without failing on a database
that already has them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

from dashboard.storage.schema import INITIAL_SCHEMA
from dashboard.storage.schema_v2 import ANALYTICS_SCHEMA, RUNS_COLUMNS_V2

Step = str | Callable[[sqlite3.Connection], None]


def _add_runs_analytics_columns(conn: sqlite3.Connection) -> None:
    """Extend the version 1 ``runs`` table with ingestion bookkeeping.

    ``ALTER TABLE ... ADD COLUMN`` is not conditional in SQLite, so existing columns are
    detected first. This keeps the migration idempotent for a database that was created
    at version 2 directly as well as one upgraded from version 1.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
    for column, definition in RUNS_COLUMNS_V2:
        if column not in existing:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {definition}")


MIGRATIONS: dict[int, tuple[Step, ...]] = {
    1: INITIAL_SCHEMA,
    2: (*ANALYTICS_SCHEMA, _add_runs_analytics_columns),
}

LATEST_VERSION = max(MIGRATIONS)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def get_current_version(conn: sqlite3.Connection) -> int | None:
    """Return the applied schema version, or None for an empty/unmanaged database."""
    try:
        if not _has_table(conn, "schema_versions"):
            return None
        row = conn.execute("SELECT MAX(version) FROM schema_versions").fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row and row[0] is not None else None


def apply_migrations(conn: sqlite3.Connection, current_version: int | None = None) -> int:
    """Bring the connection's database up to :data:`LATEST_VERSION`.

    Idempotent: re-running against an up-to-date database is a no-op.
    """
    if current_version is None:
        current_version = get_current_version(conn)
    start = 0 if current_version is None else current_version
    if start >= LATEST_VERSION:
        return start

    applied_at = datetime.now(timezone.utc).isoformat()
    for version in range(start + 1, LATEST_VERSION + 1):
        for step in MIGRATIONS.get(version, ()):
            if callable(step):
                step(conn)
            else:
                conn.execute(step)
        conn.execute(
            "INSERT OR REPLACE INTO schema_versions (version, applied_at) VALUES (?, ?)",
            (version, applied_at),
        )
    conn.commit()
    return LATEST_VERSION
