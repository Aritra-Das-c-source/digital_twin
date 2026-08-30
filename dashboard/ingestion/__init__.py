"""Downstream ingestion of completed run artifacts.

Read-only with respect to the existing system: these modules parse artifacts the
simulator and coordinated runtime have already written, and write only into the
dashboard's own database.
"""

from dashboard.ingestion.analytics_ingestor import AnalyticsIngestor, AnalyticsIngestResult
from dashboard.ingestion.bottleneck_reader import (
    BottleneckStreamSummary,
    read_bottleneck_summary,
)
from dashboard.ingestion.defect_reader import DefectStreamSummary, read_defect_summary
from dashboard.ingestion.normalizers import (
    StreamResult,
    artifact_fingerprint,
    build_topology_rows,
    read_bottleneck_stream,
    read_defect_stream,
    read_run_topology,
    read_sensor_observations,
    read_station_events,
    read_units,
)
from dashboard.ingestion.run_ingestor import (
    ClearResult,
    IncompleteRunError,
    IngestionResult,
    RunIngestor,
    factory_fingerprint,
)
from dashboard.ingestion.runtime_reader import (
    HealthView,
    health_view,
    read_run_metadata,
    read_system_health,
    read_system_manifest,
)

__all__ = [
    "AnalyticsIngestResult",
    "AnalyticsIngestor",
    "BottleneckStreamSummary",
    "ClearResult",
    "DefectStreamSummary",
    "HealthView",
    "IncompleteRunError",
    "IngestionResult",
    "RunIngestor",
    "StreamResult",
    "artifact_fingerprint",
    "build_topology_rows",
    "factory_fingerprint",
    "health_view",
    "read_bottleneck_stream",
    "read_bottleneck_summary",
    "read_defect_stream",
    "read_defect_summary",
    "read_run_metadata",
    "read_run_topology",
    "read_sensor_observations",
    "read_station_events",
    "read_system_health",
    "read_system_manifest",
    "read_units",
]
