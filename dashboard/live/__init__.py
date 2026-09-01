"""Live consumption of the prediction streams the existing runtime is writing.

This package reads; it never predicts. It tails the runtime's own JSONL output while a
run executes so the dashboard can show a station's bottleneck-probability timeline as it
is produced, and it keeps that accumulated history available once the run has finished.
"""

from dashboard.live.bottleneck_state import (
    LiveBottleneckState,
    PredictionPoint,
    StationAnalytics,
    StationSeries,
    WarningPeriod,
    parse_point,
)
from dashboard.live.session import (
    LivePredictionFeed,
    LiveRunProgress,
    LiveRunRegistry,
    LiveRunSession,
    LiveRunStatus,
    bottleneck_stream_path,
    get_registry,
)
from dashboard.live.stream import JsonlTailer, TailResult

__all__ = [
    "JsonlTailer",
    "LiveBottleneckState",
    "LivePredictionFeed",
    "LiveRunProgress",
    "LiveRunRegistry",
    "LiveRunSession",
    "LiveRunStatus",
    "PredictionPoint",
    "StationAnalytics",
    "StationSeries",
    "TailResult",
    "WarningPeriod",
    "bottleneck_stream_path",
    "get_registry",
    "parse_point",
]
