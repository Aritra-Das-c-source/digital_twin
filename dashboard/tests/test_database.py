"""Tests for dashboard.storage — SQLite database, schema, and repositories."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.domain.run import Run, RunStatus
from dashboard.storage.database import DashboardDatabase
from dashboard.storage.repositories import RunRepository


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_dashboard.db"


@pytest.fixture
def db(db_path: Path) -> DashboardDatabase:
    database = DashboardDatabase(db_path)
    database.initialize()
    return database


@pytest.fixture
def repo(db: DashboardDatabase) -> RunRepository:
    return RunRepository(db)


def _sample_run(
    run_id: str = "run_0001",
    production_day: int = 1,
    **overrides,
) -> Run:
    defaults = {
        "run_id": run_id,
        "production_day": production_day,
        "status": RunStatus.COMPLETED,
        "scenario_name": "GRADUAL",
        "scenario_description": "Gradual degradation test",
        "multiplier": 60.0,
        "factory_path": "/path/to/factory.json",
        "artifact_path": "/path/to/run_0001",
        "started_at": "2026-08-30T10:00:00",
        "completed_at": "2026-08-30T10:05:00",
        "is_demo": False,
        "metadata_json": json.dumps({"units_created": 42}),
    }
    defaults.update(overrides)
    return Run(**defaults)


class TestDashboardDatabase:
    """Test SQLite database lifecycle."""

    def test_initialize_creates_file(self, db_path: Path) -> None:
        db = DashboardDatabase(db_path)
        assert not db_path.exists()
        db.initialize()
        assert db_path.exists()

    def test_is_initialized(self, db: DashboardDatabase) -> None:
        assert db.is_initialized() is True

    def test_not_initialized_before_init(self, db_path: Path) -> None:
        db = DashboardDatabase(db_path)
        assert db.is_initialized() is False

    def test_schema_version(self, db: DashboardDatabase) -> None:
        version = db.schema_version()
        assert version is not None
        assert version >= 1

    def test_connect_returns_connection(self, db: DashboardDatabase) -> None:
        conn = db.connect()
        assert conn is not None
        conn.close()

    def test_reset_recreates_database(self, db: DashboardDatabase, db_path: Path) -> None:
        assert db_path.exists()
        db.reset()
        assert db_path.exists()
        assert db.is_initialized()
        assert db.schema_version() is not None

    def test_double_initialize_is_safe(self, db: DashboardDatabase) -> None:
        db.initialize()
        db.initialize()
        assert db.is_initialized()

    def test_initialize_creates_parent_directories(self, tmp_path: Path) -> None:
        deep_path = tmp_path / "a" / "b" / "c" / "test.db"
        db = DashboardDatabase(deep_path)
        db.initialize()
        assert deep_path.exists()


class TestRunRepository:
    """Test CRUD operations for runs."""

    def test_insert_and_get(self, repo: RunRepository) -> None:
        run = _sample_run()
        repo.insert_run(run)
        fetched = repo.get_run("run_0001")
        assert fetched is not None
        assert fetched.run_id == "run_0001"
        assert fetched.production_day == 1
        assert fetched.scenario_name == "GRADUAL"

    def test_get_nonexistent_returns_none(self, repo: RunRepository) -> None:
        assert repo.get_run("nonexistent") is None

    def test_list_runs_ordered_by_production_day(self, repo: RunRepository) -> None:
        repo.insert_run(_sample_run("run_0001", 1))
        repo.insert_run(_sample_run("run_0002", 2))
        repo.insert_run(_sample_run("run_0003", 3))
        runs = repo.list_runs()
        days = [r.production_day for r in runs]
        assert days == sorted(days, reverse=True)

    def test_list_runs_with_limit(self, repo: RunRepository) -> None:
        for i in range(5):
            repo.insert_run(_sample_run(f"run_{i:04d}", i + 1))
        runs = repo.list_runs(limit=3)
        assert len(runs) == 3

    def test_count_runs(self, repo: RunRepository) -> None:
        assert repo.count_runs() == 0
        repo.insert_run(_sample_run())
        assert repo.count_runs() == 1

    def test_next_production_day(self, repo: RunRepository) -> None:
        assert repo.next_production_day() == 1
        repo.insert_run(_sample_run("run_0001", 1))
        assert repo.next_production_day() == 2
        repo.insert_run(_sample_run("run_0002", 5))
        assert repo.next_production_day() == 6

    def test_update_run_status(self, repo: RunRepository) -> None:
        repo.insert_run(_sample_run(status=RunStatus.RUNNING))
        repo.update_run_status("run_0001", RunStatus.COMPLETED, "2026-08-30T11:00:00")
        run = repo.get_run("run_0001")
        assert run is not None
        assert run.status == RunStatus.COMPLETED

    def test_delete_run(self, repo: RunRepository) -> None:
        repo.insert_run(_sample_run())
        assert repo.count_runs() == 1
        repo.delete_run("run_0001")
        assert repo.count_runs() == 0

    def test_run_status_preserved(self, repo: RunRepository) -> None:
        for status in RunStatus:
            rid = f"run_{status.value}"
            repo.insert_run(_sample_run(run_id=rid, status=status))
            fetched = repo.get_run(rid)
            assert fetched is not None
            assert fetched.status == status

    def test_demo_flag_preserved(self, repo: RunRepository) -> None:
        repo.insert_run(_sample_run("demo_run", is_demo=True))
        run = repo.get_run("demo_run")
        assert run is not None
        assert run.is_demo is True

    def test_metadata_json_round_trip(self, repo: RunRepository) -> None:
        metadata = {"units_created": 100, "notes": "test run"}
        repo.insert_run(_sample_run(metadata_json=json.dumps(metadata)))
        run = repo.get_run("run_0001")
        assert run is not None
        parsed = json.loads(run.metadata_json)
        assert parsed["units_created"] == 100
