"""Project one completed run's artifacts into the analytical read model.

Direction of flow is one-way and strictly downstream. Nothing here writes to a simulator
run directory, a prediction stream, a health file, a factory configuration, or a model
artifact -- they are opened read-only and remain the source of truth. Everything this
module produces can be deleted and rebuilt from them.

Idempotency
-----------

``ingest(run)`` any number of times leaves the same rows behind. Two mechanisms combine:

* Each table's write is *replace*, not merge: the run's existing rows are deleted and the
  new ones inserted inside one transaction (see
  :meth:`~dashboard.storage.analytics_repository.AnalyticsRepository._replace_run_scoped`).
  Row identity is ``(run_id, source_seq)``, the record's ordinal in its append-only
  source, so the same record always lands in the same slot.
* Each source artifact's size and mtime are fingerprinted into ``ingest_cursors``. An
  unchanged run short-circuits instead of re-reading tens of megabytes of JSONL. Passing
  ``force=True`` skips the short-circuit; the result is identical either way.

Cross-check
-----------

``system_run_manifest.json`` records how many predictions and warnings the coordinated
runtime itself validated. Ingestion compares its own counts against those and reports a
mismatch rather than trusting itself -- that is a free, exact check that the projection
did not drop or duplicate records.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dashboard.analytics.alerts import derive_alerts
from dashboard.analytics.metrics import (
    compute_run_metrics,
    compute_station_metrics,
    compute_unit_metrics,
)
from dashboard.domain.run import Run
from dashboard.ingestion.normalizers import (
    artifact_fingerprint,
    read_bottleneck_stream,
    read_defect_stream,
    read_run_topology,
    read_sensor_observations,
    read_station_events,
    read_units,
)
from dashboard.ingestion.runtime_reader import read_system_health, read_system_manifest
from dashboard.storage.analytics_repository import AnalyticsRepository
from dashboard.storage.repositories import RunRepository

logger = logging.getLogger(__name__)

SOURCE_STATION_EVENTS = "station_events"
SOURCE_UNITS = "units"
SOURCE_SENSORS = "observability"
SOURCE_BOTTLENECK = "bottleneck_predictions"
SOURCE_DEFECT = "defect_predictions"
SOURCE_TOPOLOGY = "topology"

STATE_INGESTED = "INGESTED"
STATE_PARTIAL = "PARTIAL"
STATE_PENDING = "PENDING"


@dataclass
class AnalyticsIngestResult:
    """What one run's ingestion produced, and what it could not."""

    run_id: str
    skipped: bool = False
    topology_fingerprint: str | None = None
    station_count: int = 0
    queue_events: int = 0
    unit_count: int = 0
    bottleneck_records: int = 0
    defect_records: int = 0
    alert_count: int = 0
    malformed_records: int = 0
    missing_sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        if self.missing_sources:
            return STATE_PARTIAL
        return STATE_INGESTED

    @property
    def has_predictions(self) -> bool:
        return bool(self.bottleneck_records or self.defect_records)


class AnalyticsIngestor:
    """Turns completed run artifacts into the derived analytical tables."""

    def __init__(self, analytics: AnalyticsRepository, runs: RunRepository):
        self.analytics = analytics
        self.runs = runs

    # -- public API ----------------------------------------------------------------------

    def ingest_run(self, run: Run, *, force: bool = False) -> AnalyticsIngestResult:
        """Project one run. Safe to call repeatedly; see the module docstring."""
        result = AnalyticsIngestResult(run_id=run.run_id)
        run_dir = Path(run.artifact_path) if run.artifact_path else None
        predictions_dir = Path(run.predictions_path) if run.predictions_path else None

        sources = self._source_paths(run_dir, predictions_dir)
        if not force and self._is_current(run.run_id, sources):
            result.skipped = True
            return result

        # -- topology ---------------------------------------------------------------
        station_rows, zone_rows, fingerprint = ([], [], None)
        if run_dir is not None:
            station_rows, zone_rows, fingerprint = read_run_topology(run_dir)
        if not station_rows:
            result.missing_sources.append("stations.csv")
            result.warnings.append(
                "This run has no stations.csv, so its line cannot be drawn. The run "
                "history entry is kept; only its analytics are unavailable."
            )
        else:
            self._write_topology(run, fingerprint, station_rows, zone_rows)
            result.topology_fingerprint = fingerprint
            result.station_count = len(station_rows)

        # -- observed line state -----------------------------------------------------
        queue_result = (
            read_station_events(run_dir / "station_events.csv", run.run_id)
            if run_dir is not None
            else None
        )
        queue_rows = queue_result.rows if queue_result else []
        if queue_result is None or not queue_result.exists:
            result.missing_sources.append("station_events.csv")
        self.analytics.replace_queue_snapshots(run.run_id, queue_rows)
        result.queue_events = len(queue_rows)

        unit_result = (
            read_units(run_dir, run.run_id, queue_rows) if run_dir is not None else None
        )
        unit_rows = unit_result.rows if unit_result else []
        self.analytics.replace_units(run.run_id, unit_rows)
        result.unit_count = len(unit_rows)

        sensor_result = (
            read_sensor_observations(run_dir, run.run_id) if run_dir is not None else None
        )
        sensor_rows = sensor_result.rows if sensor_result else []
        self.analytics.replace_sensor_observations(run.run_id, sensor_rows)

        # -- prediction streams -------------------------------------------------------
        manifest = read_system_manifest(predictions_dir) if predictions_dir else None
        models = self._models(manifest)
        # The runtime's own run id, which differs from the dashboard's storage key.
        source_run_id = (manifest or {}).get("run_id")
        bottleneck_result = (
            read_bottleneck_stream(
                predictions_dir / "bottleneck_predictions.jsonl",
                run.run_id,
                models.get("bottleneck"),
                source_run_id=source_run_id,
            )
            if predictions_dir is not None
            else None
        )
        defect_result = (
            read_defect_stream(
                predictions_dir / "defect_predictions.jsonl",
                run.run_id,
                models.get("defect"),
                source_run_id=source_run_id,
            )
            if predictions_dir is not None
            else None
        )

        bottleneck_rows = bottleneck_result.rows if bottleneck_result else []
        defect_rows = defect_result.rows if defect_result else []
        self.analytics.replace_bottleneck_predictions(run.run_id, bottleneck_rows)
        self.analytics.replace_defect_predictions(run.run_id, defect_rows)
        result.bottleneck_records = len(bottleneck_rows)
        result.defect_records = len(defect_rows)
        result.malformed_records = sum(
            stream.malformed
            for stream in (queue_result, unit_result, bottleneck_result, defect_result)
            if stream is not None
        )

        if bottleneck_result is None or not bottleneck_result.exists:
            result.missing_sources.append("bottleneck_predictions.jsonl")
        if defect_result is None or not defect_result.exists:
            result.missing_sources.append("defect_predictions.jsonl")

        # -- derived ------------------------------------------------------------------
        alerts = derive_alerts(bottleneck_rows, defect_rows, run.run_id)
        self.analytics.replace_alerts(run.run_id, alerts)
        result.alert_count = len(alerts)

        station_metrics = compute_station_metrics(
            run.run_id, station_rows, bottleneck_rows, queue_rows, alerts
        )
        self.analytics.replace_station_metrics(run.run_id, station_metrics)
        self.analytics.replace_unit_metrics(
            run.run_id, compute_unit_metrics(run.run_id, unit_rows, defect_rows)
        )

        health = read_system_health(predictions_dir) if predictions_dir else None
        confidences = [
            row["state_confidence"]
            for row in bottleneck_rows
            if row["state_confidence"] is not None
        ]
        first_ms, last_ms = self._bounds(queue_result, bottleneck_result, defect_result)
        self.analytics.upsert_run_metrics(
            compute_run_metrics(
                run.run_id,
                stations=station_rows,
                station_metrics=station_metrics,
                units=unit_rows,
                bottleneck_count=len(bottleneck_rows),
                defect_count=len(defect_rows),
                alerts=alerts,
                first_timestamp_ms=first_ms,
                last_timestamp_ms=last_ms,
                simulated_duration_ms=run.duration_ms,
                health_status=(health or {}).get("overall_status"),
                mean_state_confidence=(
                    sum(confidences) / len(confidences) if confidences else None
                ),
            )
        )

        # `prediction_outcomes` and `model_metrics` stay empty. The structures exist so
        # later work can score predictions against observed outcomes; writing rows now
        # would mean inventing outcomes, which is exactly what must not happen.
        self.analytics.replace_prediction_outcomes(run.run_id, [])
        self.analytics.replace_model_metrics(run.run_id, [])

        result.warnings.extend(
            self._cross_check(manifest, len(bottleneck_rows), len(defect_rows))
        )

        self._record_cursors(
            run.run_id,
            sources,
            {
                SOURCE_STATION_EVENTS: (len(queue_rows), queue_result),
                SOURCE_UNITS: (len(unit_rows), unit_result),
                SOURCE_SENSORS: (len(sensor_rows), sensor_result),
                SOURCE_BOTTLENECK: (len(bottleneck_rows), bottleneck_result),
                SOURCE_DEFECT: (len(defect_rows), defect_result),
                SOURCE_TOPOLOGY: (len(station_rows), None),
            },
        )
        self.analytics.set_analytics_state(run.run_id, result.state)
        return result

    def clear_run(self, run_id: str) -> None:
        """Drop every derived row for one run, keeping its history entry."""
        self.analytics.db.clear_run(run_id)
        self.analytics.set_analytics_state(run_id, STATE_PENDING)

    # -- internals ------------------------------------------------------------------------

    @staticmethod
    def _source_paths(
        run_dir: Path | None, predictions_dir: Path | None
    ) -> dict[str, Path | None]:
        return {
            SOURCE_TOPOLOGY: run_dir / "stations.csv" if run_dir else None,
            SOURCE_STATION_EVENTS: run_dir / "station_events.csv" if run_dir else None,
            SOURCE_UNITS: run_dir / "units.csv" if run_dir else None,
            SOURCE_SENSORS: run_dir / "sensor_readings.csv" if run_dir else None,
            SOURCE_BOTTLENECK: (
                predictions_dir / "bottleneck_predictions.jsonl" if predictions_dir else None
            ),
            SOURCE_DEFECT: (
                predictions_dir / "defect_predictions.jsonl" if predictions_dir else None
            ),
        }

    def _is_current(self, run_id: str, sources: dict[str, Path | None]) -> bool:
        """True when every present artifact matches what was ingested last time."""
        cursors = {
            cursor["source"]: cursor for cursor in self.analytics.list_ingest_cursors(run_id)
        }
        if not cursors:
            return False
        for source, path in sources.items():
            cursor = cursors.get(source)
            if cursor is None:
                return False
            if cursor["fingerprint"] != (artifact_fingerprint(path) if path else None):
                return False
        return True

    def _record_cursors(
        self,
        run_id: str,
        sources: dict[str, Path | None],
        outcomes: dict[str, tuple[int, Any]],
    ) -> None:
        for source, path in sources.items():
            records, stream = outcomes.get(source, (0, None))
            stat = None
            if path is not None:
                try:
                    stat = path.stat()
                except OSError:
                    stat = None
            self.analytics.upsert_ingest_cursor(
                run_id,
                source,
                source_path=str(path) if path else None,
                source_size=stat.st_size if stat else None,
                source_mtime=stat.st_mtime if stat else None,
                fingerprint=artifact_fingerprint(path) if path else None,
                records_ingested=records,
                malformed_lines=getattr(stream, "malformed", 0) if stream else 0,
            )

    def _write_topology(
        self,
        run: Run,
        fingerprint: str | None,
        station_rows: list[dict[str, Any]],
        zone_rows: list[dict[str, Any]],
    ) -> None:
        if not fingerprint:
            return
        for row in station_rows:
            row["factory_fingerprint"] = fingerprint
        for row in zone_rows:
            row["factory_fingerprint"] = fingerprint

        self.analytics.upsert_factory(
            fingerprint,
            path=str(run.factory_path or ""),
            name=Path(run.factory_path).name if run.factory_path else None,
            station_count=len(station_rows),
            dark_zone_count=len(zone_rows),
            is_demo=run.is_demo,
            topology=None,
        )
        self.analytics.replace_stations(fingerprint, station_rows)
        self.analytics.replace_dark_zones(fingerprint, zone_rows)
        # Point the run at the topology it actually executed against, which is what the
        # Live Factory resolves through. This is deliberately the run's own stations.csv
        # rather than today's factory.json.
        self.analytics.set_run_factory_fingerprint(run.run_id, fingerprint)

    @staticmethod
    def _bounds(*streams) -> tuple[int | None, int | None]:
        firsts = [s.first_timestamp_ms for s in streams if s and s.first_timestamp_ms is not None]
        lasts = [s.last_timestamp_ms for s in streams if s and s.last_timestamp_ms is not None]
        return (min(firsts) if firsts else None, max(lasts) if lasts else None)

    @staticmethod
    def _models(manifest: dict[str, Any] | None) -> dict[str, str | None]:
        models = (manifest or {}).get("models") or {}
        return {
            "bottleneck": models.get("bottleneck"),
            "defect": models.get("defect"),
        }

    @staticmethod
    def _cross_check(
        manifest: dict[str, Any] | None, bottleneck_rows: int, defect_rows: int
    ) -> list[str]:
        """Compare ingested counts with the runtime's own validated totals.

        ``system_run_manifest.json`` already records how many predictions the coordinated
        runtime validated for each stream. If the projection disagrees, the projection is
        wrong -- and saying so is far better than serving quietly incomplete analytics.
        """
        validation = (manifest or {}).get("validation") or {}
        warnings: list[str] = []
        for stream, ingested in (
            ("bottleneck", bottleneck_rows),
            ("defect", defect_rows),
        ):
            declared = (validation.get(stream) or {}).get("predictions")
            if isinstance(declared, int) and declared != ingested:
                warnings.append(
                    f"The {stream} stream declares {declared} predictions in "
                    f"system_run_manifest.json but {ingested} were ingested. The "
                    "artifact stays authoritative; these analytics are incomplete."
                )
        return warnings
