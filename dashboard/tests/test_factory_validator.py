"""Tests for dashboard.factory.validator — factory.json structural validation."""
from __future__ import annotations

import copy

import pytest

from dashboard.factory.validator import is_valid_factory, validate_factory


def _minimal_factory() -> dict:
    """Return a minimal valid factory for testing."""
    return {
        "stations": [
            {"id": 0, "name": "Infeed", "archetype": "AUTOMATED",
             "meanCycleTimeMs": 36000, "cycleTimeCV": 0.10,
             "bufferCapacity": 0, "sensorCoverage": "HIGH", "source": True},
            {"id": 1, "name": "Weld A", "archetype": "AUTOMATED",
             "meanCycleTimeMs": 40000, "cycleTimeCV": 0.10,
             "bufferCapacity": 4, "sensorCoverage": "HIGH"},
            {"id": 2, "name": "Dispatch", "archetype": "AUTOMATED",
             "meanCycleTimeMs": 35000, "cycleTimeCV": 0.08,
             "bufferCapacity": 4, "sensorCoverage": "PARTIAL", "sink": True},
        ],
    }


class TestValidFactory:
    """Verify that valid factories pass validation."""

    def test_minimal_factory_is_valid(self) -> None:
        errors = validate_factory(_minimal_factory())
        assert errors == []

    def test_is_valid_convenience(self) -> None:
        assert is_valid_factory(_minimal_factory()) is True

    def test_with_dark_zones(self) -> None:
        factory = _minimal_factory()
        factory["darkZones"] = [
            {"id": "DZ_01", "name": "Test Zone",
             "startStationId": 1, "endStationId": 1,
             "observability": {"sensorTelemetry": True}}
        ]
        assert validate_factory(factory) == []

    def test_with_checkpoints(self) -> None:
        factory = _minimal_factory()
        factory["checkpoints"] = [
            {"id": "CP_01", "stationId": 1, "type": "RFID",
             "progress": 0.5, "reliability": 0.9, "falsePositiveRate": 0.01,
             "identifiesUnit": True}
        ]
        assert validate_factory(factory) == []


class TestInvalidFactories:
    """Verify that invalid factories are correctly rejected."""

    def test_missing_stations(self) -> None:
        errors = validate_factory({})
        assert any("stations" in e.lower() for e in errors)

    def test_empty_stations(self) -> None:
        errors = validate_factory({"stations": []})
        assert any("at least" in e.lower() or "stations" in e.lower() for e in errors)

    def test_too_few_stations(self) -> None:
        factory = _minimal_factory()
        factory["stations"] = factory["stations"][:2]
        errors = validate_factory(factory)
        assert len(errors) > 0

    def test_missing_station_id(self) -> None:
        factory = _minimal_factory()
        del factory["stations"][1]["id"]
        errors = validate_factory(factory)
        assert len(errors) > 0

    def test_invalid_archetype(self) -> None:
        factory = _minimal_factory()
        factory["stations"][1]["archetype"] = "ROBOTIC"
        errors = validate_factory(factory)
        assert any("archetype" in e.lower() for e in errors)

    def test_invalid_sensor_coverage(self) -> None:
        factory = _minimal_factory()
        factory["stations"][1]["sensorCoverage"] = "FULL"
        errors = validate_factory(factory)
        assert any("sensor" in e.lower() or "coverage" in e.lower() for e in errors)

    def test_negative_cycle_time(self) -> None:
        factory = _minimal_factory()
        factory["stations"][1]["meanCycleTimeMs"] = -100
        errors = validate_factory(factory)
        assert len(errors) > 0

    def test_no_source_station(self) -> None:
        factory = _minimal_factory()
        del factory["stations"][0]["source"]
        errors = validate_factory(factory)
        assert any("source" in e.lower() for e in errors)

    def test_no_sink_station(self) -> None:
        factory = _minimal_factory()
        del factory["stations"][2]["sink"]
        errors = validate_factory(factory)
        assert any("sink" in e.lower() for e in errors)

    def test_duplicate_station_ids(self) -> None:
        factory = _minimal_factory()
        factory["stations"][1]["id"] = 0  # duplicate
        errors = validate_factory(factory)
        assert len(errors) > 0

    def test_dark_zone_invalid_station_reference(self) -> None:
        factory = _minimal_factory()
        factory["darkZones"] = [
            {"id": "DZ_BAD", "name": "Bad Zone",
             "startStationId": 1, "endStationId": 99}
        ]
        errors = validate_factory(factory)
        assert len(errors) > 0

    def test_dark_zone_start_after_end(self) -> None:
        factory = _minimal_factory()
        factory["darkZones"] = [
            {"id": "DZ_BAD", "name": "Reversed",
             "startStationId": 2, "endStationId": 1}
        ]
        errors = validate_factory(factory)
        assert len(errors) > 0

    def test_is_valid_returns_false(self) -> None:
        assert is_valid_factory({}) is False

    def test_non_dict_input(self) -> None:
        errors = validate_factory("not a dict")  # type: ignore[arg-type]
        assert len(errors) > 0
