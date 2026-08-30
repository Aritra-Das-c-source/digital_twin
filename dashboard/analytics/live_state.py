"""Point-in-time state of the line, assembled for the Live Factory.

A :class:`LiveFactoryState` is everything needed to draw the line at one instant of the
*simulator* clock: where each unit is, how full each buffer is, what each station's
bottleneck risk was at that moment, and how much of that was directly observed rather
than reconstructed.

It is built from six indexed queries regardless of factory size -- not one per station --
because the page redraws the whole line whenever the time cursor moves.

Honesty rules baked into the model
----------------------------------

* A station with no prediction gets :data:`RISK_NO_SIGNAL`, never 0%. In this repository
  ``S14`` sits inside the DARK corridor and receives no bottleneck predictions at all,
  and the source station never receives one by design. Rendering either as "0% risk"
  would be a fabrication.
* A DARK station's buffer occupancy is ``None``, not zero. It is not observable.
* A unit inside the DARK corridor carries the particle filter's probability distribution
  over corridor stations rather than a definite position, along with the confidence that
  distribution came with.
* Risk bands are derived from each record's own ``decision_threshold``, never from a
  hard-coded probability, because the two models carry different calibrated thresholds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from dashboard.analytics.topology import FactoryTopology, StationNode

# -- risk banding ---------------------------------------------------------------------

RISK_NO_SIGNAL = "NO_SIGNAL"
RISK_NOMINAL = "NOMINAL"
RISK_ELEVATED = "ELEVATED"
RISK_WARNING = "WARNING"
RISK_CRITICAL = "CRITICAL"

#: Ordered worst-first, for "what should I look at first?".
RISK_SEVERITY_ORDER = {
    RISK_CRITICAL: 0,
    RISK_WARNING: 1,
    RISK_ELEVATED: 2,
    RISK_NOMINAL: 3,
    RISK_NO_SIGNAL: 4,
}

# -- unit placement -------------------------------------------------------------------

PLACEMENT_BUFFER = "BUFFER"
PLACEMENT_PROCESSING = "PROCESSING"
PLACEMENT_IN_TRANSIT = "IN_TRANSIT"
PLACEMENT_DARK = "DARK_CORRIDOR"
PLACEMENT_COMPLETED = "COMPLETED"

#: Maps the simulator's station event types onto where the unit is *now*.
_PLACEMENT_BY_EVENT = {
    "UNIT_ARRIVED": PLACEMENT_BUFFER,
    "PROCESSING_STARTED": PLACEMENT_PROCESSING,
    "PROCESSING_COMPLETED": PLACEMENT_IN_TRANSIT,
    "DARK_ZONE_ENTERED": PLACEMENT_DARK,
    "DARK_ZONE_EXITED": PLACEMENT_IN_TRANSIT,
}


def risk_level(
    probability: float | None, threshold: float | None, *, has_prediction: bool = True
) -> str:
    """Band a probability against the model's own decision threshold.

    ``WARNING`` is the model's boundary. ``CRITICAL`` is halfway from there to certainty
    and matches :func:`dashboard.analytics.alerts.critical_cut`, so a station shown as
    critical and an alert recorded as critical always agree. ``ELEVATED`` is half the
    threshold: measurable pressure that has not yet become actionable.
    """
    if not has_prediction or probability is None:
        return RISK_NO_SIGNAL
    if threshold is None:
        return RISK_NOMINAL
    if probability >= threshold + (1.0 - threshold) / 2.0:
        return RISK_CRITICAL
    if probability >= threshold:
        return RISK_WARNING
    if probability >= threshold / 2.0:
        return RISK_ELEVATED
    return RISK_NOMINAL


def _drivers(raw: str | None, limit: int = 3) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [item for item in parsed if isinstance(item, dict)][:limit] if isinstance(parsed, list) else []


# -- state objects ---------------------------------------------------------------------


@dataclass
class UnitState:
    """One production unit and where it is at the displayed instant."""

    unit_id: str
    placement: str
    station_id: str | None
    observed_at_ms: int | None = None
    defect_probability: float | None = None
    decision_threshold: float | None = None
    warning: bool = False
    risk: str = RISK_NO_SIGNAL
    state_confidence: float | None = None
    route: str | None = None
    data_source: str | None = None
    vehicle_model: str | None = None
    #: Particle-filter distribution over DARK corridor stations, when inside one.
    dark_station_probs: dict[str, float] = field(default_factory=dict)
    dark_most_likely_station: str | None = None
    dark_progress: float | None = None

    @property
    def is_inferred(self) -> bool:
        return self.placement == PLACEMENT_DARK or self.route == "DARK_INFERRED"

    @property
    def risk_percent(self) -> float | None:
        return None if self.defect_probability is None else self.defect_probability * 100.0


@dataclass
class QueueState:
    """The buffer feeding one station, ready to draw as beads on a segment."""

    upstream_station_id: str | None
    station_id: str
    capacity: int
    #: ``None`` where occupancy is not observable (inside a DARK corridor).
    occupancy: int | None = None
    observed_at_ms: int | None = None
    observable: bool = True
    units: list[UnitState] = field(default_factory=list)

    @property
    def pressure(self) -> float | None:
        """``occupancy / capacity``: 0 is empty, 1 is a full buffer that will block."""
        if self.occupancy is None or not self.capacity:
            return None
        return min(1.0, self.occupancy / self.capacity)

    @property
    def is_full(self) -> bool:
        return self.occupancy is not None and self.capacity > 0 and self.occupancy >= self.capacity

    @property
    def label(self) -> str:
        if not self.observable or self.occupancy is None:
            return "—"
        return f"{self.occupancy}/{self.capacity}" if self.capacity else "no buffer"


@dataclass
class StationState:
    """One station block: configuration, observed state, and predicted risk."""

    node: StationNode
    bottleneck_probability: float | None = None
    decision_threshold: float | None = None
    warning: bool = False
    risk: str = RISK_NO_SIGNAL
    state_confidence: float | None = None
    prediction_at_ms: int | None = None
    prediction_route: str | None = None
    top_drivers: list[dict[str, Any]] = field(default_factory=list)
    utilization: float | None = None
    observability: str = "UNOBSERVED"
    queue: QueueState | None = None
    processing_unit: UnitState | None = None
    open_alert_count: int = 0

    # Convenience passthroughs so rendering never reaches into `.node`.
    @property
    def station_id(self) -> str:
        return self.node.station_id

    @property
    def name(self) -> str:
        return self.node.name

    @property
    def is_dark(self) -> bool:
        return self.node.is_dark

    @property
    def has_prediction(self) -> bool:
        return self.bottleneck_probability is not None

    @property
    def risk_percent(self) -> float | None:
        return None if self.bottleneck_probability is None else self.bottleneck_probability * 100.0

    @property
    def severity_rank(self) -> int:
        return RISK_SEVERITY_ORDER.get(self.risk, 99)


@dataclass
class LiveFactoryState:
    """Everything the Live Factory draws for one run at one simulator timestamp."""

    run_id: str
    production_day: int | None
    sim_time_ms: int
    first_timestamp_ms: int
    last_timestamp_ms: int
    topology: FactoryTopology
    stations: list[StationState] = field(default_factory=list)
    units: list[UnitState] = field(default_factory=list)
    open_alerts: list[dict[str, Any]] = field(default_factory=list)
    health_status: str | None = None
    bottleneck_stream_available: bool = True
    defect_stream_available: bool = True
    notices: list[str] = field(default_factory=list)

    # -- summaries the header and the "what first?" strip need ------------------------

    @property
    def station_by_id(self) -> dict[str, StationState]:
        return {station.station_id: station for station in self.stations}

    @property
    def units_on_line(self) -> list[UnitState]:
        return [unit for unit in self.units if unit.placement != PLACEMENT_COMPLETED]

    @property
    def at_risk_units(self) -> list[UnitState]:
        return sorted(
            (unit for unit in self.units_on_line if unit.risk in (RISK_WARNING, RISK_CRITICAL)),
            key=lambda unit: unit.defect_probability or 0.0,
            reverse=True,
        )

    @property
    def pressured_stations(self) -> list[StationState]:
        """Stations to inspect first: worst risk band, then highest probability."""
        return sorted(
            (station for station in self.stations if station.risk in (RISK_WARNING, RISK_CRITICAL)),
            key=lambda station: (station.severity_rank, -(station.bottleneck_probability or 0.0)),
        )

    @property
    def congested_queues(self) -> list[QueueState]:
        return sorted(
            (
                station.queue
                for station in self.stations
                if station.queue and (station.queue.pressure or 0) >= 0.75
            ),
            key=lambda queue: -(queue.pressure or 0.0),
        )

    @property
    def unobserved_stations(self) -> list[StationState]:
        return [station for station in self.stations if station.observability == "UNOBSERVED"]

    @property
    def total_wip(self) -> int:
        return len(self.units_on_line)

    @property
    def is_empty(self) -> bool:
        return not self.stations


# -- assembly ------------------------------------------------------------------------------


def _dark_state(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_live_state(
    *,
    run_id: str,
    production_day: int | None,
    sim_time_ms: int,
    first_timestamp_ms: int,
    last_timestamp_ms: int,
    topology: FactoryTopology,
    latest_bottleneck: dict[str, dict[str, Any]],
    latest_defect: dict[str, dict[str, Any]],
    latest_queue: dict[str, dict[str, Any]],
    latest_unit_events: dict[str, dict[str, Any]],
    dark_states: dict[str, dict[str, Any]],
    unit_manifest: dict[str, dict[str, Any]],
    open_alerts: Sequence[dict[str, Any]] = (),
    station_observability: dict[str, str] | None = None,
    busy_ms: dict[str, int] | None = None,
    utilization_window_ms: int = 600_000,
    health_status: str | None = None,
    bottleneck_stream_available: bool = True,
    defect_stream_available: bool = True,
    notices: Sequence[str] = (),
) -> LiveFactoryState:
    """Assemble the snapshot. Pure: every input is already-fetched data."""
    station_observability = station_observability or {}
    busy_ms = busy_ms or {}

    alerts_by_station: dict[str, int] = {}
    for alert in open_alerts:
        if alert.get("entity_type") == "STATION":
            key = str(alert.get("entity_id"))
            alerts_by_station[key] = alerts_by_station.get(key, 0) + 1

    # -- units first: stations need to know which unit is on the machine ---------------
    units: list[UnitState] = []
    units_by_station_buffer: dict[str, list[UnitState]] = {}
    processing_by_station: dict[str, UnitState] = {}
    sink_id = next(
        (station.station_id for station in reversed(topology.stations) if station.is_sink), None
    )

    for unit_id, event in sorted(latest_unit_events.items()):
        event_type = event.get("event_type")
        placement = _PLACEMENT_BY_EVENT.get(event_type, PLACEMENT_IN_TRANSIT)
        station_id = event.get("station_id")

        if placement == PLACEMENT_IN_TRANSIT and station_id == sink_id and event_type == "PROCESSING_COMPLETED":
            placement = PLACEMENT_COMPLETED

        prediction = latest_defect.get(unit_id, {})
        probability = prediction.get("probability")
        threshold = prediction.get("decision_threshold")
        manifest = unit_manifest.get(unit_id, {})

        unit = UnitState(
            unit_id=unit_id,
            placement=placement,
            station_id=station_id,
            observed_at_ms=event.get("timestamp_ms"),
            defect_probability=probability,
            decision_threshold=threshold,
            warning=bool(prediction.get("warning")),
            risk=risk_level(probability, threshold, has_prediction=bool(prediction)),
            state_confidence=prediction.get("state_confidence"),
            route=prediction.get("route"),
            data_source=prediction.get("data_source"),
            vehicle_model=manifest.get("vehicle_model"),
        )

        if placement == PLACEMENT_DARK:
            state = _dark_state(dark_states.get(unit_id, {}).get("dark_state_json"))
            probs = state.get("station_probs")
            if isinstance(probs, dict):
                unit.dark_station_probs = {
                    str(key): float(value) for key, value in probs.items()
                }
            unit.dark_most_likely_station = state.get("most_likely_station") or station_id
            progress = state.get("progress_mean")
            unit.dark_progress = float(progress) if isinstance(progress, (int, float)) else None
            # Place the unit at the corridor station the filter thinks is most likely,
            # while keeping the whole distribution for the detail panel.
            if unit.dark_most_likely_station:
                unit.station_id = unit.dark_most_likely_station

        units.append(unit)

        if placement == PLACEMENT_BUFFER and station_id:
            units_by_station_buffer.setdefault(station_id, []).append(unit)
        elif placement == PLACEMENT_PROCESSING and station_id:
            processing_by_station[station_id] = unit

    # -- stations ------------------------------------------------------------------------
    stations: list[StationState] = []
    for node in topology.stations:
        prediction = latest_bottleneck.get(node.station_id, {})
        probability = prediction.get("probability")
        threshold = prediction.get("decision_threshold")
        observability = station_observability.get(node.station_id, "UNOBSERVED")

        reading = latest_queue.get(node.station_id)
        observable_queue = reading is not None
        queue = QueueState(
            upstream_station_id=node.upstream_station_id,
            station_id=node.station_id,
            capacity=node.buffer_capacity,
            occupancy=reading.get("occupancy") if reading else None,
            observed_at_ms=reading.get("timestamp_ms") if reading else None,
            observable=observable_queue,
            units=units_by_station_buffer.get(node.station_id, []),
        )

        window_busy = busy_ms.get(node.station_id)
        utilization = (
            min(1.0, window_busy / utilization_window_ms)
            if window_busy is not None and utilization_window_ms > 0
            else None
        )

        stations.append(
            StationState(
                node=node,
                bottleneck_probability=probability,
                decision_threshold=threshold,
                warning=bool(prediction.get("warning")),
                risk=risk_level(probability, threshold, has_prediction=bool(prediction)),
                state_confidence=prediction.get("state_confidence"),
                prediction_at_ms=prediction.get("timestamp_ms"),
                prediction_route=prediction.get("route"),
                top_drivers=_drivers(prediction.get("drivers_json")),
                utilization=utilization,
                observability=observability,
                queue=queue,
                processing_unit=processing_by_station.get(node.station_id),
                open_alert_count=alerts_by_station.get(node.station_id, 0),
            )
        )

    return LiveFactoryState(
        run_id=run_id,
        production_day=production_day,
        sim_time_ms=sim_time_ms,
        first_timestamp_ms=first_timestamp_ms,
        last_timestamp_ms=last_timestamp_ms,
        topology=topology,
        stations=stations,
        units=units,
        open_alerts=list(open_alerts),
        health_status=health_status,
        bottleneck_stream_available=bottleneck_stream_available,
        defect_stream_available=defect_stream_available,
        notices=list(notices),
    )
