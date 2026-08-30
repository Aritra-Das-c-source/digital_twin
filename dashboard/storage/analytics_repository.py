"""Repository access to the analytical read model.

All SQL for the version 2 tables lives here, alongside
:mod:`dashboard.storage.repositories`, so views and services never build a query. The
split is by lifecycle rather than by table: :class:`RunRepository` owns run history,
this class owns everything derived from a run's artifacts.

Two invariants hold for every method below.

* **Run scoping is not optional.** Every fact query filters on ``run_id`` first. The
  same ``S12`` and ``U000001`` exist in every run; a query that forgets the run silently
  merges them.
* **Writes replace, they never merge.** Each ``replace_*`` method deletes the run's rows
  for that table and re-inserts, inside one transaction. That is what makes ingestion
  idempotent no matter how many times it runs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

from dashboard.storage.database import DashboardDatabase

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def _row(cursor) -> dict[str, Any] | None:
    row = cursor.fetchone()
    return dict(row) if row else None


BOTTLENECK_COLUMNS = (
    "run_id", "source_seq", "timestamp_ms", "station_id", "vehicle_id", "zone", "route",
    "prediction_trigger", "probability", "risk_percent", "warning", "decision_threshold",
    "threshold_crossed", "state_confidence", "event_id", "event_sequence", "model_id",
    "schema_version", "drivers_json", "dark_state_json",
)

DEFECT_COLUMNS = (
    "run_id", "source_seq", "timestamp_ms", "unit_id", "station_id", "station_index",
    "final_inspection_station", "route", "prediction_trigger", "data_source",
    "probability", "risk_percent", "raw_probability", "alert_policy",
    "alert_policy_score", "decision_threshold", "threshold_crossed", "warning",
    "state_confidence", "model_id", "schema_version", "risk_drivers_json",
    "protective_drivers_json",
)

QUEUE_COLUMNS = (
    "run_id", "source_seq", "timestamp_ms", "station_id", "event_type", "unit_id",
    "occupancy", "cycle_time_ms", "dark_zone_id",
)

UNIT_COLUMNS = (
    "run_id", "unit_id", "created_at_ms", "vehicle_model", "supplier_batch",
    "first_seen_ms", "last_seen_ms", "last_station_id", "completed", "inspection_result",
)

SENSOR_COLUMNS = (
    "run_id", "station_id", "channel_kind", "channel", "observation_count",
    "first_timestamp_ms", "last_timestamp_ms",
)

STATION_COLUMNS = (
    "factory_fingerprint", "station_id", "station_index", "name", "archetype",
    "mean_cycle_time_ms", "cycle_time_cv", "buffer_capacity", "sensor_coverage",
    "is_source", "is_sink", "is_dark", "dark_zone_id", "upstream_station_id",
    "downstream_station_id",
)

DARK_ZONE_COLUMNS = (
    "factory_fingerprint", "dark_zone_id", "name", "start_station_id", "end_station_id",
    "sensor_telemetry", "manual_checks", "checkpoints",
)

ALERT_COLUMNS = (
    "alert_id", "run_id", "alert_type", "entity_type", "entity_id", "station_id",
    "started_at_ms", "ended_at_ms", "duration_ms", "severity", "status",
    "opening_probability", "peak_probability", "closing_probability",
    "decision_threshold", "min_confidence", "mean_confidence", "observation_count",
    "top_driver",
)

STATION_METRIC_COLUMNS = (
    "run_id", "station_id", "station_index", "prediction_count", "last_probability",
    "avg_probability", "peak_probability", "decision_threshold",
    "time_above_threshold_ms", "observed_span_ms", "warning_count", "critical_count",
    "alert_count", "mean_confidence", "min_confidence", "avg_queue", "peak_queue",
    "last_queue", "buffer_capacity", "utilization", "busy_ms", "units_processed",
    "observability", "computed_at",
)

UNIT_METRIC_COLUMNS = (
    "run_id", "unit_id", "prediction_count", "last_probability", "avg_probability",
    "peak_probability", "peak_at_station_id", "decision_threshold", "warning_count",
    "first_warning_ms", "mean_confidence", "last_station_id", "last_timestamp_ms",
    "largest_increase", "largest_increase_station_id", "lead_time_ms",
    "inspection_result", "computed_at",
)

RUN_METRIC_COLUMNS = (
    "run_id", "simulated_duration_ms", "first_timestamp_ms", "last_timestamp_ms",
    "units_created", "units_completed", "throughput_per_hour", "avg_lead_time_ms",
    "p95_lead_time_ms", "avg_wip", "peak_wip", "bottleneck_prediction_count",
    "defect_prediction_count", "bottleneck_alert_count", "defect_alert_count",
    "critical_alert_count", "station_count", "observed_station_count",
    "predicted_station_count", "dark_station_count", "observability_coverage",
    "mean_state_confidence", "health_status", "computed_at",
)

OUTCOME_COLUMNS = (
    "outcome_id", "run_id", "prediction_kind", "subject_type", "subject_id",
    "station_id", "predicted_at_ms", "predicted_positive", "probability", "horizon_ms",
    "outcome_time_ms", "outcome_type", "actual_outcome", "outcome_source",
    "validation_status", "lead_time_ms", "computed_at",
)

MODEL_METRIC_COLUMNS = (
    "run_id", "model_kind", "model_id", "decision_threshold", "evaluable_count",
    "censored_count", "true_positives", "false_positives", "true_negatives",
    "false_negatives", "precision_value", "recall_value", "false_alarm_rate",
    "mean_lead_time_ms", "method_version", "computed_at",
)


def _insert_sql(table: str, columns: Sequence[str]) -> str:
    placeholders = ", ".join("?" for _ in columns)
    return f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"


def _tuples(records: Iterable[dict[str, Any]], columns: Sequence[str]) -> list[tuple]:
    return [tuple(record.get(column) for column in columns) for record in records]


class AnalyticsRepository:
    """Reads and writes for the derived analytical tables."""

    def __init__(self, db: DashboardDatabase):
        self.db = db

    # -- topology --------------------------------------------------------------------

    def upsert_factory(
        self,
        fingerprint: str,
        *,
        path: str,
        name: str | None,
        station_count: int,
        dark_zone_count: int,
        is_demo: bool,
        topology: dict[str, Any] | None,
    ) -> None:
        with self.db.session() as conn:
            existing = conn.execute(
                "SELECT first_seen_at FROM factories WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            first_seen = existing["first_seen_at"] if existing else _now()
            conn.execute(
                "INSERT OR REPLACE INTO factories (fingerprint, path, name, station_count, "
                "dark_zone_count, is_demo, topology_json, first_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fingerprint,
                    path,
                    name,
                    int(station_count),
                    int(dark_zone_count),
                    1 if is_demo else 0,
                    json.dumps(topology, sort_keys=True) if topology else None,
                    first_seen,
                ),
            )

    def replace_stations(self, fingerprint: str, stations: Sequence[dict[str, Any]]) -> None:
        with self.db.session() as conn:
            conn.execute("DELETE FROM stations WHERE factory_fingerprint = ?", (fingerprint,))
            conn.executemany(
                _insert_sql("stations", STATION_COLUMNS), _tuples(stations, STATION_COLUMNS)
            )

    def replace_dark_zones(self, fingerprint: str, zones: Sequence[dict[str, Any]]) -> None:
        with self.db.session() as conn:
            conn.execute("DELETE FROM dark_zones WHERE factory_fingerprint = ?", (fingerprint,))
            conn.executemany(
                _insert_sql("dark_zones", DARK_ZONE_COLUMNS), _tuples(zones, DARK_ZONE_COLUMNS)
            )

    def get_factory(self, fingerprint: str) -> dict[str, Any] | None:
        with self.db.session() as conn:
            return _row(
                conn.execute("SELECT * FROM factories WHERE fingerprint = ?", (fingerprint,))
            )

    def list_stations(self, fingerprint: str) -> list[dict[str, Any]]:
        """Stations in line order. Order is the contract the Live Factory relies on."""
        with self.db.session() as conn:
            return _rows(
                conn.execute(
                    "SELECT * FROM stations WHERE factory_fingerprint = ? "
                    "ORDER BY station_index",
                    (fingerprint,),
                )
            )

    def list_dark_zones(self, fingerprint: str) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            return _rows(
                conn.execute(
                    "SELECT * FROM dark_zones WHERE factory_fingerprint = ? "
                    "ORDER BY start_station_id",
                    (fingerprint,),
                )
            )

    # -- bulk writes ------------------------------------------------------------------

    def replace_units(self, run_id: str, units: Sequence[dict[str, Any]]) -> None:
        self._replace_run_scoped(run_id, "units", UNIT_COLUMNS, units)

    def replace_bottleneck_predictions(
        self, run_id: str, records: Sequence[dict[str, Any]]
    ) -> None:
        self._replace_run_scoped(
            run_id, "bottleneck_predictions", BOTTLENECK_COLUMNS, records
        )

    def replace_defect_predictions(self, run_id: str, records: Sequence[dict[str, Any]]) -> None:
        self._replace_run_scoped(run_id, "defect_predictions", DEFECT_COLUMNS, records)

    def replace_queue_snapshots(self, run_id: str, records: Sequence[dict[str, Any]]) -> None:
        self._replace_run_scoped(run_id, "queue_snapshots", QUEUE_COLUMNS, records)

    def replace_sensor_observations(
        self, run_id: str, records: Sequence[dict[str, Any]]
    ) -> None:
        self._replace_run_scoped(run_id, "sensor_observations", SENSOR_COLUMNS, records)

    def replace_alerts(self, run_id: str, records: Sequence[dict[str, Any]]) -> None:
        self._replace_run_scoped(run_id, "alerts", ALERT_COLUMNS, records)

    def replace_station_metrics(self, run_id: str, records: Sequence[dict[str, Any]]) -> None:
        self._replace_run_scoped(run_id, "station_metrics", STATION_METRIC_COLUMNS, records)

    def replace_unit_metrics(self, run_id: str, records: Sequence[dict[str, Any]]) -> None:
        self._replace_run_scoped(run_id, "unit_metrics", UNIT_METRIC_COLUMNS, records)

    def replace_prediction_outcomes(
        self, run_id: str, records: Sequence[dict[str, Any]]
    ) -> None:
        self._replace_run_scoped(run_id, "prediction_outcomes", OUTCOME_COLUMNS, records)

    def replace_model_metrics(self, run_id: str, records: Sequence[dict[str, Any]]) -> None:
        self._replace_run_scoped(run_id, "model_metrics", MODEL_METRIC_COLUMNS, records)

    def upsert_run_metrics(self, metrics: dict[str, Any]) -> None:
        with self.db.session() as conn:
            conn.execute(
                _insert_sql("run_metrics", RUN_METRIC_COLUMNS),
                tuple(metrics.get(column) for column in RUN_METRIC_COLUMNS),
            )

    def _replace_run_scoped(
        self,
        run_id: str,
        table: str,
        columns: Sequence[str],
        records: Sequence[dict[str, Any]],
    ) -> None:
        """Delete this run's rows and insert the new ones in a single transaction.

        Replacing rather than merging is the whole idempotency strategy: ingesting the
        same artifact twice cannot leave stale rows behind or double a count, and a
        partially-written batch rolls back rather than leaving half a run in the table.
        """
        with self.db.session() as conn:
            conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            if records:
                conn.executemany(_insert_sql(table, columns), _tuples(records, columns))

    # -- ingest bookkeeping -------------------------------------------------------------

    def upsert_ingest_cursor(
        self,
        run_id: str,
        source: str,
        *,
        source_path: str | None,
        source_size: int | None,
        source_mtime: float | None,
        fingerprint: str | None,
        records_ingested: int,
        malformed_lines: int = 0,
    ) -> None:
        with self.db.session() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ingest_cursors (run_id, source, source_path, "
                "source_size, source_mtime, fingerprint, records_ingested, "
                "malformed_lines, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    source,
                    source_path,
                    source_size,
                    source_mtime,
                    fingerprint,
                    int(records_ingested),
                    int(malformed_lines),
                    _now(),
                ),
            )

    def get_ingest_cursor(self, run_id: str, source: str) -> dict[str, Any] | None:
        with self.db.session() as conn:
            return _row(
                conn.execute(
                    "SELECT * FROM ingest_cursors WHERE run_id = ? AND source = ?",
                    (run_id, source),
                )
            )

    def list_ingest_cursors(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            return _rows(
                conn.execute(
                    "SELECT * FROM ingest_cursors WHERE run_id = ? ORDER BY source",
                    (run_id,),
                )
            )

    def set_run_factory_fingerprint(self, run_id: str, fingerprint: str) -> None:
        """Bind a run to the topology it actually executed against.

        Recorded from the run's own ``stations.csv`` rather than from today's
        ``factory.json``, so editing the configuration later cannot retroactively redraw
        a completed run against a line it never ran on.
        """
        with self.db.session() as conn:
            conn.execute(
                "UPDATE runs SET factory_fingerprint = ?, updated_at = ? WHERE run_id = ?",
                (fingerprint, _now(), run_id),
            )

    def set_analytics_state(self, run_id: str, state: str) -> None:
        with self.db.session() as conn:
            conn.execute(
                "UPDATE runs SET analytics_state = ?, analytics_ingested_at = ? "
                "WHERE run_id = ?",
                (state, _now() if state == "INGESTED" else None, run_id),
            )

    # -- prediction history ---------------------------------------------------------------

    def bottleneck_history(
        self,
        run_id: str,
        *,
        station_id: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        sql = [
            "SELECT * FROM bottleneck_predictions WHERE run_id = ?",
        ]
        params: list[Any] = [run_id]
        if station_id:
            sql.append("AND station_id = ?")
            params.append(station_id)
        if start_ms is not None:
            sql.append("AND timestamp_ms >= ?")
            params.append(int(start_ms))
        if end_ms is not None:
            sql.append("AND timestamp_ms <= ?")
            params.append(int(end_ms))
        sql.append("ORDER BY timestamp_ms, source_seq")
        if limit:
            sql.append("LIMIT ?")
            params.append(int(limit))
        with self.db.session() as conn:
            return _rows(conn.execute(" ".join(sql), params))

    def defect_history(
        self,
        run_id: str,
        *,
        unit_id: str | None = None,
        station_id: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        sql = ["SELECT * FROM defect_predictions WHERE run_id = ?"]
        params: list[Any] = [run_id]
        if unit_id:
            sql.append("AND unit_id = ?")
            params.append(unit_id)
        if station_id:
            sql.append("AND station_id = ?")
            params.append(station_id)
        if start_ms is not None:
            sql.append("AND timestamp_ms >= ?")
            params.append(int(start_ms))
        if end_ms is not None:
            sql.append("AND timestamp_ms <= ?")
            params.append(int(end_ms))
        sql.append("ORDER BY timestamp_ms, source_seq")
        if limit:
            sql.append("LIMIT ?")
            params.append(int(limit))
        with self.db.session() as conn:
            return _rows(conn.execute(" ".join(sql), params))

    # -- point-in-time state ---------------------------------------------------------------
    #
    # Each of these is one query with a window function rather than one query per entity.
    # That is deliberate: the Live Factory redraws the whole line on every interaction, and
    # a per-station query would be 32-50 round trips per frame.

    def latest_bottleneck_by_station(
        self, run_id: str, at_ms: int | None = None
    ) -> dict[str, dict[str, Any]]:
        """Most recent bottleneck prediction at or before ``at_ms``, keyed by station."""
        sql = (
            "SELECT * FROM (SELECT *, ROW_NUMBER() OVER ("
            "  PARTITION BY station_id ORDER BY timestamp_ms DESC, source_seq DESC"
            ") AS rn FROM bottleneck_predictions WHERE run_id = ?"
        )
        params: list[Any] = [run_id]
        if at_ms is not None:
            sql += " AND timestamp_ms <= ?"
            params.append(int(at_ms))
        sql += ") WHERE rn = 1"
        with self.db.session() as conn:
            return {row["station_id"]: dict(row) for row in conn.execute(sql, params)}

    def latest_defect_by_unit(
        self, run_id: str, at_ms: int | None = None
    ) -> dict[str, dict[str, Any]]:
        """Most recent defect prediction at or before ``at_ms``, keyed by unit."""
        sql = (
            "SELECT * FROM (SELECT *, ROW_NUMBER() OVER ("
            "  PARTITION BY unit_id ORDER BY timestamp_ms DESC, source_seq DESC"
            ") AS rn FROM defect_predictions WHERE run_id = ?"
        )
        params: list[Any] = [run_id]
        if at_ms is not None:
            sql += " AND timestamp_ms <= ?"
            params.append(int(at_ms))
        sql += ") WHERE rn = 1"
        with self.db.session() as conn:
            return {row["unit_id"]: dict(row) for row in conn.execute(sql, params)}

    def latest_queue_by_station(
        self, run_id: str, at_ms: int | None = None
    ) -> dict[str, dict[str, Any]]:
        """Last observed buffer occupancy per station at or before ``at_ms``.

        Only rows that actually carry an occupancy count: ``DARK_ZONE_ENTERED`` and
        ``DARK_ZONE_EXITED`` are boundary markers, not buffer readings.
        """
        sql = (
            "SELECT * FROM (SELECT *, ROW_NUMBER() OVER ("
            "  PARTITION BY station_id ORDER BY timestamp_ms DESC, source_seq DESC"
            ") AS rn FROM queue_snapshots WHERE run_id = ? AND occupancy IS NOT NULL"
            "  AND event_type IN ('UNIT_ARRIVED', 'PROCESSING_STARTED', 'PROCESSING_COMPLETED')"
        )
        params: list[Any] = [run_id]
        if at_ms is not None:
            sql += " AND timestamp_ms <= ?"
            params.append(int(at_ms))
        sql += ") WHERE rn = 1"
        with self.db.session() as conn:
            return {row["station_id"]: dict(row) for row in conn.execute(sql, params)}

    def latest_unit_events(
        self, run_id: str, at_ms: int | None = None
    ) -> dict[str, dict[str, Any]]:
        """Last station event per unit at or before ``at_ms``.

        This is how a unit's position on the line is recovered: the event type says
        whether it is waiting in a buffer, being processed, has left a station, or has
        entered the DARK corridor.
        """
        sql = (
            "SELECT * FROM (SELECT *, ROW_NUMBER() OVER ("
            "  PARTITION BY unit_id ORDER BY timestamp_ms DESC, source_seq DESC"
            ") AS rn FROM queue_snapshots WHERE run_id = ?"
            "  AND unit_id IS NOT NULL AND unit_id <> ''"
        )
        params: list[Any] = [run_id]
        if at_ms is not None:
            sql += " AND timestamp_ms <= ?"
            params.append(int(at_ms))
        sql += ") WHERE rn = 1"
        with self.db.session() as conn:
            return {row["unit_id"]: dict(row) for row in conn.execute(sql, params)}

    def latest_dark_state_by_vehicle(
        self, run_id: str, at_ms: int | None = None
    ) -> dict[str, dict[str, Any]]:
        """Most recent DARK-corridor reconstruction per vehicle at or before ``at_ms``.

        Only ``DARK_CORRIDOR`` rows carry ``dark_state_json``. It holds the particle
        filter's distribution over the corridor's stations, which is the only positional
        information that exists for a vehicle inside an unobserved corridor.
        """
        sql = (
            "SELECT * FROM (SELECT *, ROW_NUMBER() OVER ("
            "  PARTITION BY vehicle_id ORDER BY timestamp_ms DESC, source_seq DESC"
            ") AS rn FROM bottleneck_predictions WHERE run_id = ?"
            "  AND dark_state_json IS NOT NULL AND vehicle_id IS NOT NULL"
        )
        params: list[Any] = [run_id]
        if at_ms is not None:
            sql += " AND timestamp_ms <= ?"
            params.append(int(at_ms))
        sql += ") WHERE rn = 1"
        with self.db.session() as conn:
            return {row["vehicle_id"]: dict(row) for row in conn.execute(sql, params)}

    def station_event_window(
        self, run_id: str, station_id: str, *, end_ms: int, limit: int = 25
    ) -> list[dict[str, Any]]:
        """Most recent station events before ``end_ms``, newest first."""
        with self.db.session() as conn:
            return _rows(
                conn.execute(
                    "SELECT * FROM queue_snapshots WHERE run_id = ? AND station_id = ? "
                    "AND timestamp_ms <= ? ORDER BY timestamp_ms DESC, source_seq DESC "
                    "LIMIT ?",
                    (run_id, station_id, int(end_ms), int(limit)),
                )
            )

    def unit_event_path(self, run_id: str, unit_id: str, *, end_ms: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM queue_snapshots WHERE run_id = ? AND unit_id = ?"
        params: list[Any] = [run_id, unit_id]
        if end_ms is not None:
            sql += " AND timestamp_ms <= ?"
            params.append(int(end_ms))
        sql += " ORDER BY timestamp_ms, source_seq"
        with self.db.session() as conn:
            return _rows(conn.execute(sql, params))

    def busy_stations(self, run_id: str, at_ms: int, window_ms: int) -> dict[str, int]:
        """Processing time per station over ``[at_ms - window_ms, at_ms]``.

        Uses ``cycle_time_ms`` recorded on ``PROCESSING_COMPLETED``, which the simulator
        writes directly, rather than pairing start/stop events in Python.
        """
        with self.db.session() as conn:
            return {
                row["station_id"]: int(row["busy_ms"] or 0)
                for row in conn.execute(
                    "SELECT station_id, SUM(COALESCE(cycle_time_ms, 0)) AS busy_ms "
                    "FROM queue_snapshots WHERE run_id = ? AND event_type = "
                    "'PROCESSING_COMPLETED' AND timestamp_ms <= ? AND timestamp_ms > ? "
                    "GROUP BY station_id",
                    (run_id, int(at_ms), int(at_ms) - int(window_ms)),
                )
            }

    # -- derived reads -------------------------------------------------------------------

    def get_run_metrics(self, run_id: str) -> dict[str, Any] | None:
        with self.db.session() as conn:
            return _row(conn.execute("SELECT * FROM run_metrics WHERE run_id = ?", (run_id,)))

    def get_station_metrics(
        self, run_id: str, station_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM station_metrics WHERE run_id = ?"
        params: list[Any] = [run_id]
        if station_id:
            sql += " AND station_id = ?"
            params.append(station_id)
        sql += " ORDER BY station_index"
        with self.db.session() as conn:
            return _rows(conn.execute(sql, params))

    def get_unit_metrics(
        self, run_id: str, unit_id: str | None = None, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM unit_metrics WHERE run_id = ?"
        params: list[Any] = [run_id]
        if unit_id:
            sql += " AND unit_id = ?"
            params.append(unit_id)
        sql += " ORDER BY peak_probability DESC, unit_id"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self.db.session() as conn:
            return _rows(conn.execute(sql, params))

    def get_alerts(
        self,
        run_id: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        alert_type: str | None = None,
        severity: str | None = None,
        active_at_ms: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        sql = ["SELECT * FROM alerts WHERE run_id = ?"]
        params: list[Any] = [run_id]
        if entity_type:
            sql.append("AND entity_type = ?")
            params.append(entity_type)
        if entity_id:
            sql.append("AND entity_id = ?")
            params.append(entity_id)
        if alert_type:
            sql.append("AND alert_type = ?")
            params.append(alert_type)
        if severity:
            sql.append("AND severity = ?")
            params.append(severity)
        if active_at_ms is not None:
            # Open at this instant: started at or before, and either never closed or
            # closed after.
            sql.append("AND started_at_ms <= ? AND (ended_at_ms IS NULL OR ended_at_ms > ?)")
            params.extend([int(active_at_ms), int(active_at_ms)])
        sql.append("ORDER BY started_at_ms DESC")
        if limit:
            sql.append("LIMIT ?")
            params.append(int(limit))
        with self.db.session() as conn:
            return _rows(conn.execute(" ".join(sql), params))

    def get_sensor_observations(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            return _rows(
                conn.execute(
                    "SELECT * FROM sensor_observations WHERE run_id = ? "
                    "ORDER BY station_id, channel_kind, channel",
                    (run_id,),
                )
            )

    def get_units(self, run_id: str, unit_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM units WHERE run_id = ?"
        params: list[Any] = [run_id]
        if unit_id:
            sql += " AND unit_id = ?"
            params.append(unit_id)
        sql += " ORDER BY unit_id"
        with self.db.session() as conn:
            return _rows(conn.execute(sql, params))

    def get_prediction_outcomes(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            return _rows(
                conn.execute(
                    "SELECT * FROM prediction_outcomes WHERE run_id = ? "
                    "ORDER BY predicted_at_ms",
                    (run_id,),
                )
            )

    def get_model_metrics(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            return _rows(
                conn.execute(
                    "SELECT * FROM model_metrics WHERE run_id = ? ORDER BY model_kind",
                    (run_id,),
                )
            )

    # -- bounds and counts ------------------------------------------------------------------

    def run_time_bounds(self, run_id: str) -> tuple[int | None, int | None]:
        """Earliest and latest simulator timestamp across every ingested source."""
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT MIN(lo) AS lo, MAX(hi) AS hi FROM ("
                "  SELECT MIN(timestamp_ms) lo, MAX(timestamp_ms) hi FROM queue_snapshots WHERE run_id = ?"
                "  UNION ALL"
                "  SELECT MIN(timestamp_ms), MAX(timestamp_ms) FROM bottleneck_predictions WHERE run_id = ?"
                "  UNION ALL"
                "  SELECT MIN(timestamp_ms), MAX(timestamp_ms) FROM defect_predictions WHERE run_id = ?"
                ")",
                (run_id, run_id, run_id),
            ).fetchone()
        if row is None:
            return None, None
        return row["lo"], row["hi"]

    def count_rows(self, table: str, run_id: str | None = None) -> int:
        with self.db.session() as conn:
            if run_id is None:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            else:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (run_id,)
                ).fetchone()
        return int(row[0]) if row else 0
