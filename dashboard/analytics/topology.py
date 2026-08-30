"""Factory topology as data.

The line's shape -- how many stations, in what order, with what buffers, and which of
them sit inside an unobserved corridor -- is read from the ``stations`` and
``dark_zones`` tables and handed to the UI as an object. No rendering code computes a
station's neighbours, its position, or its label, which is what lets a factory with a
different station count, different naming, or a different corridor layout render without
touching the Live Factory at all.

The topology is scoped to a *factory fingerprint*, not to a run. Runs reference the
fingerprint they executed against, so editing ``factory.json`` afterwards changes what
future runs look like without retroactively redrawing old ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence


@dataclass(frozen=True)
class DarkZone:
    """One configured corridor of stations with no direct internal telemetry."""

    dark_zone_id: str
    name: str | None
    start_station_id: str
    end_station_id: str
    sensor_telemetry: bool
    manual_checks: bool
    checkpoints: bool
    #: Every station label inside the corridor, in line order.
    station_ids: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.name or self.dark_zone_id

    @property
    def observability_summary(self) -> str:
        """What evidence the corridor contract does allow through."""
        allowed = [
            name
            for name, enabled in (
                ("sensor telemetry", self.sensor_telemetry),
                ("manual checks", self.manual_checks),
                ("checkpoints", self.checkpoints),
            )
            if enabled
        ]
        if not allowed:
            return "No evidence of any kind leaves this corridor."
        return "Boundary evidence: " + ", ".join(allowed) + "."


@dataclass(frozen=True)
class StationNode:
    """One station and its place on the line."""

    station_id: str
    station_index: int
    name: str
    archetype: str
    buffer_capacity: int
    sensor_coverage: str
    mean_cycle_time_ms: int
    cycle_time_cv: float
    is_source: bool
    is_sink: bool
    is_dark: bool
    dark_zone_id: str | None
    upstream_station_id: str | None
    downstream_station_id: str | None
    #: 0-based position in the rendered line. Equals the ordinal, not the station id, so
    #: a factory with non-contiguous ids still lays out correctly.
    position: int = 0

    @property
    def zone(self) -> str:
        return "DARK" if self.is_dark else "LIGHT"

    @property
    def has_buffer(self) -> bool:
        return self.buffer_capacity > 0

    @property
    def short_name(self) -> str:
        """A name that fits inside a station block without wrapping to three lines."""
        return self.name if len(self.name) <= 22 else self.name[:21] + "…"


@dataclass(frozen=True)
class QueueSegment:
    """The buffer that sits between two stations.

    In this simulator a station's ``bufferCapacity`` is the queue *feeding* it, and
    ``queue_length_after`` on its events is that queue's occupancy. So the segment
    drawn between ``S(n-1)`` and ``S(n)`` belongs to ``S(n)``: its capacity sets the
    segment's physical length and its occupancy sets how many beads sit in it.
    """

    upstream_station_id: str | None
    downstream_station_id: str
    capacity: int


@dataclass
class FactoryTopology:
    """An ordered line of stations, with corridors and buffers resolved."""

    fingerprint: str
    name: str | None = None
    path: str | None = None
    stations: list[StationNode] = field(default_factory=list)
    dark_zones: list[DarkZone] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_id = {station.station_id: station for station in self.stations}
        self._zones_by_id = {zone.dark_zone_id: zone for zone in self.dark_zones}

    # -- lookups ----------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.stations)

    def __iter__(self) -> Iterator[StationNode]:
        return iter(self.stations)

    def __bool__(self) -> bool:
        return bool(self.stations)

    def station(self, station_id: str) -> StationNode | None:
        return self._by_id.get(station_id)

    def dark_zone(self, dark_zone_id: str | None) -> DarkZone | None:
        return self._zones_by_id.get(dark_zone_id) if dark_zone_id else None

    def zone_for_station(self, station_id: str) -> DarkZone | None:
        station = self.station(station_id)
        return self.dark_zone(station.dark_zone_id) if station else None

    @property
    def station_ids(self) -> list[str]:
        return [station.station_id for station in self.stations]

    @property
    def dark_station_ids(self) -> set[str]:
        return {station.station_id for station in self.stations if station.is_dark}

    def downstream_of(self, station_id: str) -> StationNode | None:
        station = self.station(station_id)
        if station is None or station.downstream_station_id is None:
            return None
        return self.station(station.downstream_station_id)

    # -- derived structure ---------------------------------------------------------------

    def queue_segments(self) -> list[QueueSegment]:
        """One segment per station that has an inbound buffer, in line order."""
        return [
            QueueSegment(
                upstream_station_id=station.upstream_station_id,
                downstream_station_id=station.station_id,
                capacity=station.buffer_capacity,
            )
            for station in self.stations
            if station.upstream_station_id is not None
        ]

    def coverage_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for station in self.stations:
            counts[station.sensor_coverage] = counts.get(station.sensor_coverage, 0) + 1
        return counts


def build_topology(
    fingerprint: str,
    station_rows: Sequence[dict[str, Any]],
    dark_zone_rows: Sequence[dict[str, Any]] = (),
    *,
    name: str | None = None,
    path: str | None = None,
) -> FactoryTopology:
    """Assemble a topology from ``stations`` and ``dark_zones`` rows.

    Rows are sorted by ``station_index`` here rather than trusting the caller: line order
    is the one thing the Live Factory cannot get wrong, since an out-of-order line makes
    every spatial claim on the page false.
    """
    ordered = sorted(station_rows, key=lambda row: row["station_index"])
    stations = [
        StationNode(
            station_id=row["station_id"],
            station_index=int(row["station_index"]),
            name=row["name"],
            archetype=row["archetype"],
            buffer_capacity=int(row["buffer_capacity"] or 0),
            sensor_coverage=row["sensor_coverage"],
            mean_cycle_time_ms=int(row["mean_cycle_time_ms"] or 0),
            cycle_time_cv=float(row["cycle_time_cv"] or 0.0),
            is_source=bool(row["is_source"]),
            is_sink=bool(row["is_sink"]),
            is_dark=bool(row["is_dark"]),
            dark_zone_id=row["dark_zone_id"],
            upstream_station_id=row["upstream_station_id"],
            downstream_station_id=row["downstream_station_id"],
            position=position,
        )
        for position, row in enumerate(ordered)
    ]

    members: dict[str, list[str]] = {}
    for station in stations:
        if station.dark_zone_id:
            members.setdefault(station.dark_zone_id, []).append(station.station_id)

    zones = [
        DarkZone(
            dark_zone_id=row["dark_zone_id"],
            name=row["name"],
            start_station_id=row["start_station_id"],
            end_station_id=row["end_station_id"],
            sensor_telemetry=bool(row["sensor_telemetry"]),
            manual_checks=bool(row["manual_checks"]),
            checkpoints=bool(row["checkpoints"]),
            station_ids=tuple(members.get(row["dark_zone_id"], [])),
        )
        for row in dark_zone_rows
    ]

    return FactoryTopology(
        fingerprint=fingerprint, name=name, path=path, stations=stations, dark_zones=zones
    )
