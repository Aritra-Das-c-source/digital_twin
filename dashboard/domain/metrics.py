"""Metrics domain model — placeholder for future dashboard analytics."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass
class RunMetricsSummary:
    """Aggregate metrics for a single production run — placeholder."""
    run_id: str
    bottleneck_prediction_count: int = 0
    defect_prediction_count: int = 0
    bottleneck_warning_count: int = 0
    defect_warning_count: int = 0
    metadata: dict[str, Any] | None = None
