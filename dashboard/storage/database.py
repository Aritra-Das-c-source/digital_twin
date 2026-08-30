from __future__ import annotations

import sqlite3
from pathlib import Path

from dashboard.storage.migrations import apply_migrations, get_current_version

class DashboardDatabase:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            current_version = get_current_version(conn)
            apply_migrations(conn, current_version)

    def reset(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()
        self.initialize()

    def is_initialized(self) -> bool:
        if not self.db_path.exists():
            return False
        try:
            with self.connect() as conn:
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
                )
                return cursor.fetchone() is not None
        except sqlite3.Error:
            return False

    def schema_version(self) -> int | None:
        if not self.db_path.exists():
            return None
        try:
            with self.connect() as conn:
                return get_current_version(conn)
        except sqlite3.Error:
            return None
