"""Station domain model representing a factory assembly station."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Station:
    """Represents a factory station."""

    id: int
    name: str
    archetype: str
    mean_cycle_time_ms: int
    cycle_time_cv: float
    buffer_capacity: int
    sensor_coverage: str
    is_source: bool = False
    is_sink: bool = False

    @classmethod
    def from_factory_dict(cls, data: dict[str, Any]) -> Station:
        """Create a Station instance from a factory.json dictionary entry."""
        return cls(
            id=int(data["id"]),
            name=str(data["name"]),
            archetype=str(data["archetype"]),
            mean_cycle_time_ms=int(data.get("meanCycleTimeMs", data.get("mean_cycle_time_ms", 0))),
            cycle_time_cv=float(data.get("cycleTimeCV", data.get("cycle_time_cv", 0.0))),
            buffer_capacity=int(data.get("bufferCapacity", data.get("buffer_capacity", 0))),
            sensor_coverage=str(data.get("sensorCoverage", data.get("sensor_coverage", "NONE"))),
            is_source=bool(data.get("source", data.get("is_source", False))),
            is_sink=bool(data.get("sink", data.get("is_sink", False))),
        )
