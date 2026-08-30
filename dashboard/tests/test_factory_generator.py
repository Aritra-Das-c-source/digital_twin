"""Tests for dashboard.factory.generator — demo factory generation."""
from __future__ import annotations

import pytest

from dashboard.factory.generator import generate_demo_factory
from dashboard.factory.validator import is_valid_factory, validate_factory


class TestDemoFactoryGeneration:
    """Verify the deterministic demo factory generator."""

    def test_generator_returns_dict(self) -> None:
        factory = generate_demo_factory(seed=42)
        assert isinstance(factory, dict)

    def test_station_count_in_range(self) -> None:
        factory = generate_demo_factory(seed=42)
        stations = factory["stations"]
        assert 30 <= len(stations) <= 50, f"Expected 30-50 stations, got {len(stations)}"

    def test_deterministic_with_same_seed(self) -> None:
        a = generate_demo_factory(seed=99)
        b = generate_demo_factory(seed=99)
        assert a == b

    def test_different_seeds_produce_different_factories(self) -> None:
        a = generate_demo_factory(seed=1)
        b = generate_demo_factory(seed=2)
        # At least some stations should differ in cycle times
        a_times = [s["meanCycleTimeMs"] for s in a["stations"]]
        b_times = [s["meanCycleTimeMs"] for s in b["stations"]]
        assert a_times != b_times

    def test_passes_validator(self) -> None:
        factory = generate_demo_factory(seed=42)
        errors = validate_factory(factory)
        assert errors == [], f"Validation errors: {errors}"

    def test_is_valid(self) -> None:
        factory = generate_demo_factory(seed=42)
        assert is_valid_factory(factory)

    def test_has_source_and_sink(self) -> None:
        factory = generate_demo_factory(seed=42)
        stations = factory["stations"]
        sources = [s for s in stations if s.get("source")]
        sinks = [s for s in stations if s.get("sink")]
        assert len(sources) == 1, f"Expected 1 source, got {len(sources)}"
        assert len(sinks) == 1, f"Expected 1 sink, got {len(sinks)}"

    def test_has_dark_zones(self) -> None:
        factory = generate_demo_factory(seed=42)
        dark_zones = factory.get("darkZones", [])
        assert len(dark_zones) >= 1, "Expected at least 1 dark zone"

    def test_dark_zone_max_corridor_length(self) -> None:
        """Dark zone corridors must be at most 3 stations wide."""
        factory = generate_demo_factory(seed=42)
        for zone in factory.get("darkZones", []):
            span = zone["endStationId"] - zone["startStationId"] + 1
            assert span <= 4, (
                f"Dark zone {zone['id']} spans {span} stations (max allowed is 4 including boundary)"
            )

    def test_has_checkpoints(self) -> None:
        factory = generate_demo_factory(seed=42)
        checkpoints = factory.get("checkpoints", [])
        assert len(checkpoints) >= 2

    def test_mixed_archetypes(self) -> None:
        factory = generate_demo_factory(seed=42)
        archetypes = {s["archetype"] for s in factory["stations"]}
        assert "AUTOMATED" in archetypes
        assert "MANUAL" in archetypes
        assert "INSPECTION" in archetypes

    def test_mixed_sensor_coverage(self) -> None:
        factory = generate_demo_factory(seed=42)
        coverages = {s["sensorCoverage"] for s in factory["stations"]}
        assert "HIGH" in coverages
        assert "PARTIAL" in coverages
        assert "NONE" in coverages

    def test_manual_stations_have_none_coverage(self) -> None:
        factory = generate_demo_factory(seed=42)
        for station in factory["stations"]:
            if station["archetype"] == "MANUAL":
                assert station["sensorCoverage"] == "NONE", (
                    f"Manual station {station['name']} should have NONE sensor coverage"
                )

    def test_station_ids_sequential(self) -> None:
        factory = generate_demo_factory(seed=42)
        ids = [s["id"] for s in factory["stations"]]
        assert ids == list(range(len(ids)))

    def test_marked_as_generated(self) -> None:
        factory = generate_demo_factory(seed=42)
        assert factory.get("_generated") is True
        assert factory.get("_generator") == "dashboard-demo"
        assert factory.get("_seed") == 42

    def test_valid_cycle_times(self) -> None:
        factory = generate_demo_factory(seed=42)
        for station in factory["stations"]:
            assert station["meanCycleTimeMs"] > 0
            assert 0 < station["cycleTimeCV"] < 1.0
            assert station["bufferCapacity"] >= 0

    def test_multiple_seeds_all_valid(self) -> None:
        """Verify several different seeds all produce valid factories."""
        for seed in [1, 7, 42, 100, 999]:
            factory = generate_demo_factory(seed=seed)
            errors = validate_factory(factory)
            assert errors == [], f"Seed {seed} produced validation errors: {errors}"
