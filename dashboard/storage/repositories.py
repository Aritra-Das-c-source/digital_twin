from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from dashboard.domain.run import Run, RunStatus
from dashboard.storage.database import DashboardDatabase


class RunRepository:
    def __init__(self, db: DashboardDatabase):
        self.db = db

    def insert_run(self, run: Run) -> None:
        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(run.metadata) if run.metadata else None
        
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, production_day, status, scenario_name, scenario_description,
                    multiplier, factory_path, artifact_path, started_at, completed_at,
                    is_demo, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.production_day,
                    run.status.value,
                    run.scenario_name,
                    run.scenario_description,
                    run.multiplier,
                    run.factory_path,
                    run.artifact_path,
                    run.started_at,
                    run.completed_at,
                    1 if run.is_demo else 0,
                    metadata_json,
                    now,
                    now,
                )
            )
            conn.commit()

    def _row_to_run(self, row: Any) -> Run:
        metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        return Run(
            id=row["run_id"],
            production_day=row["production_day"],
            status=RunStatus(row["status"]),
            scenario_name=row["scenario_name"],
            scenario_description=row["scenario_description"],
            multiplier=row["multiplier"],
            factory_path=row["factory_path"],
            artifact_path=row["artifact_path"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            is_demo=bool(row["is_demo"]),
            metadata=metadata,
        )

    def get_run(self, run_id: str) -> Run | None:
        with self.db.connect() as conn:
            cursor = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_run(row)

    def list_runs(self, limit: int = 100, offset: int = 0) -> list[Run]:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM runs ORDER BY production_day DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )
            return [self._row_to_run(row) for row in cursor.fetchall()]

    def update_run_status(self, run_id: str, status: RunStatus, completed_at: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            if completed_at is not None:
                conn.execute(
                    """
                    UPDATE runs 
                    SET status = ?, completed_at = ?, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (status.value, completed_at, now, run_id)
                )
            else:
                conn.execute(
                    """
                    UPDATE runs 
                    SET status = ?, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (status.value, now, run_id)
                )
            conn.commit()

    def count_runs(self) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM runs")
            row = cursor.fetchone()
            return row[0] if row else 0

    def next_production_day(self) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute("SELECT MAX(production_day) FROM runs")
            row = cursor.fetchone()
            return (row[0] + 1) if row and row[0] is not None else 1

    def delete_run(self, run_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            conn.commit()
