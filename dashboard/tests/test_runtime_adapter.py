"""Tests for dashboard.orchestration.existing_runtime_adapter."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.orchestration.existing_runtime_adapter import ExistingRuntimeAdapter


@pytest.fixture
def project_root() -> Path:
    """Return the actual project root for integration-level adapter tests."""
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def adapter(project_root: Path) -> ExistingRuntimeAdapter:
    return ExistingRuntimeAdapter(project_root)


class TestExistingRuntimeAdapter:
    """Test the adapter's read-only discovery operations."""

    def test_default_factory_path(self, adapter: ExistingRuntimeAdapter) -> None:
        path = adapter.default_factory_path()
        assert path.name == "factory.json"
        assert "simulation" in str(path) or "config" in str(path)

    def test_discover_factory_finds_existing(self, adapter: ExistingRuntimeAdapter) -> None:
        factory = adapter.discover_factory()
        # The real factory.json should exist in the test repo
        if factory is not None:
            assert factory.name == "factory.json"
            assert factory.is_file()

    def test_is_completed_run_on_empty_dir(self, adapter: ExistingRuntimeAdapter, tmp_path: Path) -> None:
        assert adapter.is_completed_run(tmp_path) is False

    def test_is_completed_run_with_required_files(self, adapter: ExistingRuntimeAdapter, tmp_path: Path) -> None:
        run = tmp_path / "run_0001"
        run.mkdir()
        for name in ("stations.csv", "units.csv", "station_events.csv", "run_metadata.json"):
            (run / name).write_text("test", encoding="utf-8")
        assert adapter.is_completed_run(run) is True

    def test_is_system_completed_run_missing_extra(self, adapter: ExistingRuntimeAdapter, tmp_path: Path) -> None:
        run = tmp_path / "run_0001"
        run.mkdir()
        # Only basic files, not system-level files
        for name in ("stations.csv", "units.csv", "station_events.csv", "run_metadata.json"):
            (run / name).write_text("test", encoding="utf-8")
        assert adapter.is_system_completed_run(run) is False

    def test_is_system_completed_run_with_all_files(self, adapter: ExistingRuntimeAdapter, tmp_path: Path) -> None:
        run = tmp_path / "run_0001"
        run.mkdir()
        all_files = (
            "stations.csv", "units.csv", "station_events.csv", "run_metadata.json",
            "runtime_events.csv", "dz.csv", "station_checkpoints.csv",
        )
        for name in all_files:
            (run / name).write_text("test", encoding="utf-8")
        assert adapter.is_system_completed_run(run) is True

    def test_read_run_metadata_returns_none_for_missing(self, adapter: ExistingRuntimeAdapter, tmp_path: Path) -> None:
        assert adapter.read_run_metadata(tmp_path) is None

    def test_read_run_metadata_parses_json(self, adapter: ExistingRuntimeAdapter, tmp_path: Path) -> None:
        metadata = {"units_created": 100, "duration_ms": 28800000}
        (tmp_path / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        result = adapter.read_run_metadata(tmp_path)
        assert result is not None
        assert result["units_created"] == 100

    def test_read_system_health_returns_none_for_missing(self, adapter: ExistingRuntimeAdapter, tmp_path: Path) -> None:
        assert adapter.read_system_health(tmp_path) is None

    def test_list_completed_runs_on_empty_dir(self, adapter: ExistingRuntimeAdapter, tmp_path: Path) -> None:
        result = adapter.list_completed_runs(tmp_path)
        assert result == []

    def test_list_completed_runs_finds_runs(self, adapter: ExistingRuntimeAdapter, tmp_path: Path) -> None:
        for i in range(3):
            run = tmp_path / f"run_{i:04d}"
            run.mkdir()
            for name in ("stations.csv", "units.csv", "station_events.csv", "run_metadata.json"):
                (run / name).write_text("test", encoding="utf-8")
        result = adapter.list_completed_runs(tmp_path)
        assert len(result) == 3

    def test_prepare_random_run_not_implemented(self, adapter: ExistingRuntimeAdapter) -> None:
        with pytest.raises(NotImplementedError):
            adapter.prepare_random_run()

    def test_start_run_not_implemented(self, adapter: ExistingRuntimeAdapter) -> None:
        with pytest.raises(NotImplementedError):
            adapter.start_run()
