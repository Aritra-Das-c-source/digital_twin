"""Analytics and domain services.

The boundary between storage and the UI::

    artifacts -> ingestion -> SQLite read model -> [ this package ] -> views

Views call :class:`~dashboard.analytics.service.AnalyticsService` and nothing below it.
They do not open artifacts, write SQL, or compute metrics.
"""

from dashboard.analytics.alerts import (
    BOTTLENECK,
    DEFECT,
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    STATION,
    STATUS_OPEN,
    STATUS_RESOLVED,
    UNIT,
    critical_cut,
    derive_alerts,
    derive_bottleneck_alerts,
    derive_defect_alerts,
)
from dashboard.analytics.live_state import (
    LiveFactoryState,
    PLACEMENT_BUFFER,
    PLACEMENT_COMPLETED,
    PLACEMENT_DARK,
    PLACEMENT_IN_TRANSIT,
    PLACEMENT_PROCESSING,
    RISK_CRITICAL,
    RISK_ELEVATED,
    RISK_NOMINAL,
    RISK_NO_SIGNAL,
    RISK_WARNING,
    QueueState,
    StationState,
    UnitState,
    build_live_state,
    risk_level,
)
from dashboard.analytics.metrics import (
    compute_run_metrics,
    compute_station_metrics,
    compute_unit_metrics,
    compute_wip_profile,
)
from dashboard.analytics.service import AnalyticsService, RunSummary, StationHealth
from dashboard.analytics.topology import (
    DarkZone,
    FactoryTopology,
    QueueSegment,
    StationNode,
    build_topology,
)

__all__ = [
    "AnalyticsService",
    "BOTTLENECK",
    "DEFECT",
    "DarkZone",
    "FactoryTopology",
    "LiveFactoryState",
    "PLACEMENT_BUFFER",
    "PLACEMENT_COMPLETED",
    "PLACEMENT_DARK",
    "PLACEMENT_IN_TRANSIT",
    "PLACEMENT_PROCESSING",
    "QueueSegment",
    "QueueState",
    "RISK_CRITICAL",
    "RISK_ELEVATED",
    "RISK_NOMINAL",
    "RISK_NO_SIGNAL",
    "RISK_WARNING",
    "RunSummary",
    "SEVERITY_CRITICAL",
    "SEVERITY_WARNING",
    "STATION",
    "STATUS_OPEN",
    "STATUS_RESOLVED",
    "StationHealth",
    "StationNode",
    "StationState",
    "UNIT",
    "UnitState",
    "build_live_state",
    "build_topology",
    "compute_run_metrics",
    "compute_station_metrics",
    "compute_unit_metrics",
    "compute_wip_profile",
    "critical_cut",
    "derive_alerts",
    "derive_bottleneck_alerts",
    "derive_defect_alerts",
    "risk_level",
]
