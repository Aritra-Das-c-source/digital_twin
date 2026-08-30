"""Vehicle domain model — placeholder for future dashboard features."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Vehicle:
    """Represents a production unit/vehicle in the factory."""
    unit_id: str
    vehicle_model: str | None = None
