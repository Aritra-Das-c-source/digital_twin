"""The analytics/domain layer.

This is the only thing the views are allowed to call for data. Views do not open
artifacts, do not write SQL, and do not compute metrics; they ask a question here and
render the answer. The rule matters most for the Live Factory, which redraws the whole
line whenever the time cursor moves -- if that path went through JSONL parsing it would
re-read tens of megabytes per interaction.

Everything returned is already-derived: metrics were computed at ingestion time,
predictions are indexed by ``(run_id, entity, timestamp_ms)``, and point-in-time state
comes from window-function queries rather than per-station round trips.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from dashboard.analytics.live_state import (
    LiveFactoryState,
    RISK_NO_SIGNAL,
    UnitState,
    build_live_state,
    risk_level,
)
from dashboard.analytics.topology import FactoryTopology, build_topology
from dashboard.domain.run import Run
from dashboard.storage.analytics_repository import AnalyticsRepository
from dashboard.storage.repositories import RunRepository

logger = logging.getLogger(__name__)

#: Window used for the Live Factory's rolling utilization figure. Ten simulated minutes
#: matches the feature windows the bottleneck model itself was trained on
#: (``RECENT_MS`` in ``bottlenecks_prediction/training/build_causal_datasets.py``), so
#: the number on a station block is comparable to the ones the model reasons about.
UTILIZATION_WINDOW_MS = 600_000


@dataclass
class RunSummary:
    """Header-level facts about one run: identity, scale, health, and what was ingested."""

    run: Run
    metrics: dict[str, Any] | None = None
    ingest_sources: list[dict[str, Any]] = field(default_factory=list)
    first_timestamp_ms: int | None = None
    last_timestamp_ms: int | None = None

    @property
    def run_id(self) -> str:
        return self.run.run_id

    @property
    def production_day(self) -> int:
        return self.run.production_day

    @property
    def is_analysed(self) -> bool:
        return self.metrics is not None

    @property
    def health_status(self) -> str | None:
        return (self.metrics or {}).get("health_status")

    @property
    def has_bottleneck_stream(self) -> bool:
        return bool((self.metrics or {}).get("bottleneck_prediction_count"))

    @property
    def has_defect_stream(self) -> bool:
        return bool((self.metrics or {}).get("defect_prediction_count"))


@dataclass
class StationHealth:
    """How well one station was actually observed, separate from how it performed."""

    station_id: str
    name: str
    sensor_coverage: str
    zone: str
    observability: str
    prediction_count: int
    mean_confidence: float | None
    min_confidence: float | None
    channels: list[dict[str, Any]] = field(default_factory=list)

    @property
    def observation_total(self) -> int:
        return sum(int(channel.get("observation_count") or 0) for channel in self.channels)

    @property
    def channel_kinds(self) -> list[str]:
        return sorted({str(channel.get("channel_kind")) for channel in self.channels})

    @property
    def is_instrumented(self) -> bool:
        return any(channel.get("channel_kind") == "SENSOR" for channel in self.channels)

    @property
    def is_manual_only(self) -> bool:
        kinds = set(self.channel_kinds)
        return bool(kinds) and "SENSOR" not in kinds


class AnalyticsService:
    """Read-side services over the analytical read model."""

    def __init__(self, runs: RunRepository, analytics: AnalyticsRepository):
        self.runs = runs
        self.analytics = analytics

    # -- runs --------------------------------------------------------------------------

    def list_runs(self, limit: int = 200) -> list[Run]:
        return self.runs.list_runs(limit=limit)

    def get_run(self, run_id: str) -> Run | None:
        return self.runs.get_run(run_id)

    def latest_analysed_run(self) -> Run | None:
        """Most recent run that has ingested analytics, falling back to most recent.

        The Live Factory needs a run it can actually draw. A run recorded in history but
        never ingested has no stations, no queues and no predictions, so preferring an
        analysed one avoids opening on an empty line when a usable run exists.
        """
        for run in self.runs.list_runs(limit=50):
            if self.analytics.get_run_metrics(run.run_id) is not None:
                return run
        return self.runs.latest_run()

    def get_run_summary(self, run_id: str) -> RunSummary | None:
        run = self.runs.get_run(run_id)
        if run is None:
            return None
        first, last = self.analytics.run_time_bounds(run_id)
        return RunSummary(
            run=run,
            metrics=self.analytics.get_run_metrics(run_id),
            ingest_sources=self.analytics.list_ingest_cursors(run_id),
            first_timestamp_ms=first,
            last_timestamp_ms=last,
        )

    def get_run_metrics(self, run_id: str) -> dict[str, Any] | None:
        return self.analytics.get_run_metrics(run_id)

    def get_run_time_bounds(self, run_id: str) -> tuple[int, int]:
        """Simulator clock range for a run, defaulting to an empty range."""
        first, last = self.analytics.run_time_bounds(run_id)
        return int(first or 0), int(last or 0)

    def get_run_comparison(self, run_ids: Sequence[str]) -> list[dict[str, Any]]:
        """Side-by-side run metrics, one row per run, oldest production day first.

        Runs without ingested analytics are returned with their identity and null
        metrics rather than being dropped, so a comparison never silently omits a run
        the user explicitly selected.
        """
        rows: list[dict[str, Any]] = []
        for run_id in run_ids:
            run = self.runs.get_run(run_id)
            if run is None:
                continue
            metrics = self.analytics.get_run_metrics(run_id) or {}
            rows.append(
                {
                    "run_id": run_id,
                    "production_day": run.production_day,
                    "status": run.status.value,
                    "is_demo": run.is_demo,
                    **{key: value for key, value in metrics.items() if key != "run_id"},
                }
            )
        rows.sort(key=lambda row: row["production_day"])
        return rows

    # -- topology ------------------------------------------------------------------------

    def get_topology(self, run_id: str) -> FactoryTopology:
        """The line the run executed against.

        Resolved through the run's recorded factory fingerprint, so a run keeps rendering
        against its own topology after ``factory.json`` changes.
        """
        run = self.runs.get_run(run_id)
        fingerprint = run.factory_fingerprint if run else None
        if not fingerprint:
            return build_topology("", [], [])
        factory = self.analytics.get_factory(fingerprint) or {}
        return build_topology(
            fingerprint,
            self.analytics.list_stations(fingerprint),
            self.analytics.list_dark_zones(fingerprint),
            name=factory.get("name"),
            path=factory.get("path"),
        )

    # -- metrics --------------------------------------------------------------------------

    def get_station_metrics(
        self, run_id: str, station_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self.analytics.get_station_metrics(run_id, station_id)

    def get_unit_metrics(
        self, run_id: str, unit_id: str | None = None, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        return self.analytics.get_unit_metrics(run_id, unit_id, limit=limit)

    def get_station_health(
        self, run_id: str, station_id: str | None = None
    ) -> list[StationHealth]:
        """Observability per station: configured coverage versus evidence that arrived.

        Configured ``sensorCoverage`` is an intention. ``channels`` is what the run
        actually produced. A station can be declared HIGH coverage and still emit
        nothing, and the problem statement's uneven-instrumentation constraint is exactly
        that gap -- so both are reported and neither is used to infer the other.
        """
        topology = self.get_topology(run_id)
        metrics = {
            row["station_id"]: row for row in self.analytics.get_station_metrics(run_id)
        }
        channels: dict[str, list[dict[str, Any]]] = {}
        for observation in self.analytics.get_sensor_observations(run_id):
            channels.setdefault(observation["station_id"], []).append(observation)

        health: list[StationHealth] = []
        for node in topology.stations:
            if station_id and node.station_id != station_id:
                continue
            metric = metrics.get(node.station_id, {})
            health.append(
                StationHealth(
                    station_id=node.station_id,
                    name=node.name,
                    sensor_coverage=node.sensor_coverage,
                    zone=node.zone,
                    observability=metric.get("observability", "UNOBSERVED"),
                    prediction_count=int(metric.get("prediction_count") or 0),
                    mean_confidence=metric.get("mean_confidence"),
                    min_confidence=metric.get("min_confidence"),
                    channels=channels.get(node.station_id, []),
                )
            )
        return health

    def get_sensor_coverage(self, run_id: str) -> dict[str, Any]:
        """Run-level observability picture, configured and observed side by side."""
        health = self.get_station_health(run_id)
        configured: dict[str, int] = {}
        observed: dict[str, int] = {}
        for station in health:
            configured[station.sensor_coverage] = configured.get(station.sensor_coverage, 0) + 1
            observed[station.observability] = observed.get(station.observability, 0) + 1
        return {
            "stations": health,
            "configured_coverage": configured,
            "observed_state": observed,
            "instrumented_count": sum(1 for station in health if station.is_instrumented),
            "manual_only_count": sum(1 for station in health if station.is_manual_only),
            "unobserved_count": sum(
                1 for station in health if station.observability == "UNOBSERVED"
            ),
            "station_count": len(health),
        }

    # -- prediction history ------------------------------------------------------------------

    def get_bottleneck_history(
        self,
        run_id: str,
        station_id: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.analytics.bottleneck_history(
            run_id, station_id=station_id, start_ms=start_ms, end_ms=end_ms, limit=limit
        )

    def get_defect_history(
        self,
        run_id: str,
        unit_id: str | None = None,
        station_id: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.analytics.defect_history(
            run_id,
            unit_id=unit_id,
            station_id=station_id,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=limit,
        )

    # -- alerts ---------------------------------------------------------------------------

    def get_alerts(
        self,
        run_id: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        *,
        alert_type: str | None = None,
        severity: str | None = None,
        active_at_ms: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.analytics.get_alerts(
            run_id,
            entity_type=entity_type,
            entity_id=entity_id,
            alert_type=alert_type,
            severity=severity,
            active_at_ms=active_at_ms,
            limit=limit,
        )

    # -- live state --------------------------------------------------------------------------

    def get_live_state(self, run_id: str, sim_time_ms: int | None = None) -> LiveFactoryState:
        """The line at one instant of the simulator clock.

        Six indexed queries regardless of how many stations the factory has.
        ``sim_time_ms`` defaults to the run's last observation, which is the state the
        line ended in.
        """
        run = self.runs.get_run(run_id)
        first, last = self.get_run_time_bounds(run_id)
        at_ms = last if sim_time_ms is None else max(first, min(int(sim_time_ms), last))

        topology = self.get_topology(run_id)
        metrics = self.analytics.get_run_metrics(run_id)
        station_metrics = {
            row["station_id"]: row for row in self.analytics.get_station_metrics(run_id)
        }

        notices: list[str] = []
        if not topology:
            notices.append(
                "This run has no ingested factory topology, so the line cannot be drawn. "
                "Rebuild dashboard history from artifacts to populate it."
            )

        return build_live_state(
            run_id=run_id,
            production_day=run.production_day if run else None,
            sim_time_ms=at_ms,
            first_timestamp_ms=first,
            last_timestamp_ms=last,
            topology=topology,
            latest_bottleneck=self.analytics.latest_bottleneck_by_station(run_id, at_ms),
            latest_defect=self.analytics.latest_defect_by_unit(run_id, at_ms),
            latest_queue=self.analytics.latest_queue_by_station(run_id, at_ms),
            latest_unit_events=self.analytics.latest_unit_events(run_id, at_ms),
            dark_states=self.analytics.latest_dark_state_by_vehicle(run_id, at_ms),
            unit_manifest={
                unit["unit_id"]: unit for unit in self.analytics.get_units(run_id)
            },
            open_alerts=self.analytics.get_alerts(run_id, active_at_ms=at_ms),
            station_observability={
                station_id: row.get("observability", "UNOBSERVED")
                for station_id, row in station_metrics.items()
            },
            busy_ms=self.analytics.busy_stations(run_id, at_ms, UTILIZATION_WINDOW_MS),
            utilization_window_ms=UTILIZATION_WINDOW_MS,
            health_status=(metrics or {}).get("health_status"),
            bottleneck_stream_available=bool((metrics or {}).get("bottleneck_prediction_count")),
            defect_stream_available=bool((metrics or {}).get("defect_prediction_count")),
            notices=notices,
        )

    # -- detail panels -----------------------------------------------------------------------

    def get_station_detail(
        self, run_id: str, station_id: str, at_ms: int | None = None
    ) -> dict[str, Any]:
        """Everything the station drawer shows, in one call."""
        first, last = self.get_run_time_bounds(run_id)
        end = last if at_ms is None else int(at_ms)
        metrics = self.analytics.get_station_metrics(run_id, station_id)
        history = self.analytics.bottleneck_history(run_id, station_id=station_id, end_ms=end)
        topology = self.get_topology(run_id)
        health = self.get_station_health(run_id, station_id)
        return {
            "station": topology.station(station_id),
            "dark_zone": topology.zone_for_station(station_id),
            "metrics": metrics[0] if metrics else None,
            "health": health[0] if health else None,
            "history": history,
            "events": self.analytics.station_event_window(run_id, station_id, end_ms=end),
            "alerts": self.analytics.get_alerts(
                run_id, entity_type="STATION", entity_id=station_id
            ),
        }

    def get_unit_detail(
        self, run_id: str, unit_id: str, at_ms: int | None = None
    ) -> dict[str, Any]:
        """Everything the unit drawer shows, in one call.

        ``largest_increase_station_id`` is included as a *suspected upstream source*: the
        station the unit was at when its defect probability rose most sharply. It is a
        contribution signal from the model, not a demonstrated cause, and the view labels
        it that way.
        """
        first, last = self.get_run_time_bounds(run_id)
        end = last if at_ms is None else int(at_ms)
        history = self.analytics.defect_history(run_id, unit_id=unit_id, end_ms=end)
        metrics = self.analytics.get_unit_metrics(run_id, unit_id)
        units = self.analytics.get_units(run_id, unit_id)
        latest = history[-1] if history else {}
        return {
            "unit": units[0] if units else None,
            "metrics": metrics[0] if metrics else None,
            "history": history,
            "latest": latest,
            "risk_drivers": _load_drivers(latest.get("risk_drivers_json")),
            "protective_drivers": _load_drivers(latest.get("protective_drivers_json")),
            "path": self.analytics.unit_event_path(run_id, unit_id, end_ms=end),
            "alerts": self.analytics.get_alerts(run_id, entity_type="UNIT", entity_id=unit_id),
        }

    def get_unit_state_at(self, run_id: str, unit_id: str, at_ms: int) -> UnitState | None:
        """Placement and risk band for one unit, without building the whole line."""
        events = self.analytics.latest_unit_events(run_id, at_ms)
        event = events.get(unit_id)
        if event is None:
            return None
        prediction = self.analytics.latest_defect_by_unit(run_id, at_ms).get(unit_id, {})
        probability = prediction.get("probability")
        threshold = prediction.get("decision_threshold")
        from dashboard.analytics.live_state import _PLACEMENT_BY_EVENT

        return UnitState(
            unit_id=unit_id,
            placement=_PLACEMENT_BY_EVENT.get(event.get("event_type"), "IN_TRANSIT"),
            station_id=event.get("station_id"),
            observed_at_ms=event.get("timestamp_ms"),
            defect_probability=probability,
            decision_threshold=threshold,
            warning=bool(prediction.get("warning")),
            risk=risk_level(probability, threshold, has_prediction=bool(prediction))
            if prediction
            else RISK_NO_SIGNAL,
            state_confidence=prediction.get("state_confidence"),
            route=prediction.get("route"),
        )


def _load_drivers(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []
