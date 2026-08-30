"""Run domain model representing a simulation or historical production run."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RunStatus(str, Enum):
    """Execution status of a production run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


@dataclass
class Run:
    """Represents a single production run."""

    run_id: str
    production_day: int
    status: RunStatus
    scenario_name: str | None = None
    scenario_description: str | None = None
    multiplier: float = 60.0
    factory_path: str = ""
    artifact_path: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    is_demo: bool = False
    metadata_json: str | None = None
