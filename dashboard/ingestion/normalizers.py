"""Turn authoritative artifacts into rows of the analytical read model.

Every function here is a pure projection: it reads a file the simulator or the
coordinated runtime already wrote and returns rows. Nothing is inferred, nothing is
smoothed, nothing is invented, and no artifact is ever modified.

Two contract details drive the shape of the output.

* ``warning`` is the actionable alert on both streams and is copied verbatim. It is
  never recomputed from probability and threshold, because
  ``DASHBOARD_CONTRACTS.md`` section 4 documents that a defect ``warning`` is
  deliberately suppressed once a vehicle reaches final inspection -- a record can carry
  ``threshold_crossed = true`` with ``warning = false`` and both are true.
* ``source_seq`` is the record's 0-based ordinal within its file. The prediction streams
  and ``station_events.csv`` are append-only, so that ordinal is a stable identity for
  the record and makes re-ingestion idempotent without hashing 71 MB of JSON.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Station event types that carry a real buffer reading in ``queue_length_after``.
#: ``DARK_ZONE_ENTERED`` / ``DARK_ZONE_EXITED`` are corridor boundary markers.
BUFFER_EVENT_TYPES = frozenset(
    {"UNIT_ARRIVED", "PROCESSING_STARTED", "PROCESSING_COMPLETED"}
)

DARK_ENTER = "DARK_ZONE_ENTERED"
DARK_EXIT = "DARK_ZONE_EXITED"


@dataclass
class StreamResult:
    """Rows projected from one artifact, plus what was rejected getting there."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    malformed: int = 0
    exists: bool = False
    path: str | None = None
    first_timestamp_ms: int | None = None
    last_timestamp_ms: int | None = None

    @property
    def count(self) -> int:
        return len(self.rows)


def artifact_fingerprint(path: str | Path) -> str | None:
    """Cheap identity for an artifact: size + mtime + name.

    Content hashing a 71 MB JSONL on every startup would cost more than the ingest it is
    meant to skip. Size and mtime change on any write the pipeline makes, which is the
    only case that matters here -- and a wrong answer costs a redundant re-ingest, not
    incorrect data, because ingestion replaces rather than merges.
    """
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return None
    digest = hashlib.sha256(
        f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
    ).hexdigest()
    return digest[:16]


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # drop NaN


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _flag(value: Any) -> int:
    return 1 if value is True or value == "true" or value == 1 else 0


def _track_bounds(result: StreamResult, timestamp: int | None) -> None:
    if timestamp is None:
        return
    if result.first_timestamp_ms is None or timestamp < result.first_timestamp_ms:
        result.first_timestamp_ms = timestamp
    if result.last_timestamp_ms is None or timestamp > result.last_timestamp_ms:
        result.last_timestamp_ms = timestamp


def _iter_jsonl(path: Path, result: StreamResult):
    """Yield ``(ordinal, record)`` for every well-formed object in a JSONL file.

    Malformed lines are counted and skipped rather than aborting the ingest: a
    half-written last line is a normal state for a stream that is still being appended
    to. The ordinal counts *accepted* records, so ids stay dense and stable.
    """
    ordinal = 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                result.malformed += 1
                continue
            if not isinstance(record, dict):
                result.malformed += 1
                continue
            yield ordinal, record
            ordinal += 1


def _top_drivers(raw: Any, limit: int = 5) -> str | None:
    """Compact the SHAP driver list to what a detail panel actually shows.

    Full explanation blocks are large and already live in the JSONL, which stays
    authoritative. Storing the top few keeps the database an order of magnitude smaller
    while still answering "why is this station flagged?".
    """
    if not isinstance(raw, list) or not raw:
        return None
    drivers = []
    for entry in raw[:limit]:
        if not isinstance(entry, dict):
            continue
        drivers.append(
            {
                "feature": entry.get("feature"),
                "label": entry.get("label") or entry.get("feature"),
                "value": entry.get("value", entry.get("feature_value")),
                "contribution": entry.get("shap_log_odds", entry.get("shap_value_raw")),
                "direction": entry.get("direction") or entry.get("effect"),
            }
        )
    return json.dumps(drivers) if drivers else None


# -- prediction streams ------------------------------------------------------------------


def read_bottleneck_stream(
    path: str | Path,
    run_id: str,
    model_id: str | None = None,
    *,
    source_run_id: str | None = None,
) -> StreamResult:
    """Project ``bottleneck_predictions.jsonl`` into ``bottleneck_predictions`` rows.

    Two run identities are in play and conflating them is a real hazard. ``run_id`` is
    the *dashboard's* key for the run -- derived from the artifact directory, so
    ``production_day_0013/run_0001`` -- and is what every row is stored under.
    ``source_run_id`` is the *runtime's* own id inside the records, which for the same
    run is ``production_day_0013``. They are different namespaces and neither is wrong.

    The contamination guard therefore checks the stream against itself: every record must
    carry the same runtime run id, either the one supplied by
    ``system_run_manifest.json`` or the first one seen. A file holding two runs' records
    is rejected record by record instead of silently merging two production days.
    """
    path = Path(path)
    result = StreamResult(path=str(path))
    if not path.is_file():
        return result
    result.exists = True
    expected_source = source_run_id

    for ordinal, record in _iter_jsonl(path, result):
        record_run = _as_text(record.get("run_id"))
        if record_run is not None:
            if expected_source is None:
                expected_source = record_run
            elif record_run != expected_source:
                result.malformed += 1
                continue
        timestamp = _as_int(record.get("timestamp_ms"))
        station = _as_text(record.get("station_id"))
        if timestamp is None or station is None:
            result.malformed += 1
            continue
        probability = _as_float(record.get("bottleneck_probability"))
        threshold = _as_float(record.get("decision_threshold"))
        explanation = record.get("explanation")
        diagnostics = record.get("diagnostics")
        dark_state = None
        if isinstance(diagnostics, dict) and isinstance(diagnostics.get("dashboard_state"), dict):
            dark_state = json.dumps(diagnostics["dashboard_state"])
        _track_bounds(result, timestamp)
        result.rows.append(
            {
                "run_id": run_id,
                "source_seq": ordinal,
                "timestamp_ms": timestamp,
                "station_id": station,
                "vehicle_id": _as_text(record.get("vehicle_id")),
                "zone": _as_text(record.get("zone")),
                "route": _as_text(record.get("route")),
                "prediction_trigger": _as_text(record.get("prediction_trigger")),
                "probability": probability,
                "risk_percent": _as_float(record.get("bottleneck_risk_percent")),
                "warning": _flag(record.get("warning")),
                "decision_threshold": threshold,
                # The bottleneck contract has no explicit `threshold_crossed`; its
                # `warning` *is* `probability >= decision_threshold`. Recording the
                # comparison keeps the column meaningful across both streams.
                "threshold_crossed": (
                    1 if probability is not None and threshold is not None and probability >= threshold else 0
                ),
                "state_confidence": _as_float(record.get("state_confidence")),
                "event_id": _as_text(record.get("event_id")),
                "event_sequence": _as_int(record.get("event_sequence")),
                "model_id": model_id,
                "schema_version": _as_text(record.get("schema_version")),
                "drivers_json": _top_drivers(
                    explanation.get("top_drivers") if isinstance(explanation, dict) else None
                ),
                "dark_state_json": dark_state,
            }
        )
    return result


def read_defect_stream(
    path: str | Path,
    run_id: str,
    model_id: str | None = None,
    *,
    source_run_id: str | None = None,
) -> StreamResult:
    """Project ``defect_predictions.jsonl`` into ``defect_predictions`` rows.

    ``run_id`` is the dashboard's storage key, ``source_run_id`` the runtime's own id
    inside the records; see :func:`read_bottleneck_stream` for why they differ and how
    the cross-run guard uses them.
    """
    path = Path(path)
    result = StreamResult(path=str(path))
    if not path.is_file():
        return result
    result.exists = True
    expected_source = source_run_id

    for ordinal, record in _iter_jsonl(path, result):
        record_run = _as_text(record.get("run_id"))
        if record_run is not None:
            if expected_source is None:
                expected_source = record_run
            elif record_run != expected_source:
                result.malformed += 1
                continue
        timestamp = _as_int(record.get("timestamp_ms"))
        unit = _as_text(record.get("unit_id"))
        if timestamp is None or unit is None:
            result.malformed += 1
            continue
        _track_bounds(result, timestamp)
        result.rows.append(
            {
                "run_id": run_id,
                "source_seq": ordinal,
                "timestamp_ms": timestamp,
                "unit_id": unit,
                "station_id": _as_text(record.get("station_id")),
                "station_index": _as_int(record.get("station_index")),
                "final_inspection_station": _as_text(record.get("final_inspection_station")),
                "route": _as_text(record.get("route")),
                "prediction_trigger": _as_text(record.get("prediction_trigger")),
                "data_source": _as_text(record.get("data_source")),
                "probability": _as_float(record.get("defect_probability")),
                "risk_percent": _as_float(record.get("defect_risk_percent")),
                "raw_probability": _as_float(record.get("raw_defect_probability")),
                "alert_policy": _as_text(record.get("alert_policy")),
                "alert_policy_score": _as_float(record.get("alert_policy_score")),
                "decision_threshold": _as_float(record.get("decision_threshold")),
                # Copied, never derived: the defect runtime owns both of these and they
                # legitimately disagree at the final inspection station.
                "threshold_crossed": _flag(record.get("threshold_crossed")),
                "warning": _flag(record.get("warning")),
                "state_confidence": _as_float(record.get("state_confidence")),
                "model_id": model_id,
                "schema_version": _as_text(record.get("schema_version")),
                "risk_drivers_json": _top_drivers(record.get("top_risk_drivers")),
                "protective_drivers_json": _top_drivers(record.get("top_protective_drivers")),
            }
        )
    return result


# -- simulator artifacts -------------------------------------------------------------------


def read_station_events(path: str | Path, run_id: str) -> StreamResult:
    """Project ``station_events.csv`` into ``queue_snapshots`` rows.

    This is the only source of buffer occupancy in the whole system: neither prediction
    stream carries it, so without this projection the Live Factory has no queues to draw.
    """
    path = Path(path)
    result = StreamResult(path=str(path))
    if not path.is_file():
        return result
    result.exists = True

    with path.open(encoding="utf-8", newline="") as stream:
        ordinal = 0
        for record in csv.DictReader(stream):
            timestamp = _as_int(record.get("timestamp_ms"))
            station = _as_text(record.get("station_id"))
            event_type = _as_text(record.get("event_type"))
            if timestamp is None or station is None or event_type is None:
                result.malformed += 1
                continue
            occupancy = _as_int(record.get("queue_length_after"))
            _track_bounds(result, timestamp)
            result.rows.append(
                {
                    "run_id": run_id,
                    "source_seq": ordinal,
                    "timestamp_ms": timestamp,
                    "station_id": station,
                    "event_type": event_type,
                    "unit_id": _as_text(record.get("unit_id")),
                    "occupancy": occupancy if event_type in BUFFER_EVENT_TYPES else None,
                    "cycle_time_ms": (
                        _as_int(record.get("cycle_time_ms"))
                        if event_type == "PROCESSING_COMPLETED"
                        else None
                    ),
                    "dark_zone_id": _as_text(record.get("dark_zone_id")),
                }
            )
            ordinal += 1
    return result


def read_units(
    run_dir: str | Path, run_id: str, station_events: list[dict[str, Any]]
) -> StreamResult:
    """Project ``units.csv`` plus observed positions into ``units`` rows.

    ``units.csv`` gives identity; the station event stream gives where each unit was last
    seen and whether it left the line. Units inside the DARK corridor are last seen at the
    corridor entry marker, which is exactly the truth available -- their internal position
    is not observable and is not guessed at here.
    """
    run_dir = Path(run_dir)
    path = run_dir / "units.csv"
    result = StreamResult(path=str(path))

    observed: dict[str, dict[str, Any]] = {}
    sink_completions: set[str] = set()
    last_event_type: dict[str, str] = {}
    for event in station_events:
        unit = event.get("unit_id")
        if not unit:
            continue
        entry = observed.setdefault(
            unit, {"first_seen_ms": event["timestamp_ms"], "last_seen_ms": None, "last_station_id": None}
        )
        entry["last_seen_ms"] = event["timestamp_ms"]
        entry["last_station_id"] = event["station_id"]
        last_event_type[unit] = event["event_type"]

    inspections = _read_inspection_results(run_dir)

    if not path.is_file():
        # No units.csv: still record whatever the event stream observed, so the Live
        # Factory can place units even for a run whose unit manifest is missing.
        for unit_id, entry in sorted(observed.items()):
            result.rows.append(
                {
                    "run_id": run_id,
                    "unit_id": unit_id,
                    "created_at_ms": None,
                    "vehicle_model": None,
                    "supplier_batch": None,
                    "completed": 0,
                    "inspection_result": inspections.get(unit_id),
                    **entry,
                }
            )
        return result

    result.exists = True
    sink_station = _last_station_label(station_events)
    with path.open(encoding="utf-8", newline="") as stream:
        for record in csv.DictReader(stream):
            unit_id = _as_text(record.get("unit_id"))
            if unit_id is None:
                result.malformed += 1
                continue
            entry = observed.get(unit_id, {})
            last_station = entry.get("last_station_id")
            completed = (
                last_station is not None
                and sink_station is not None
                and last_station == sink_station
                and last_event_type.get(unit_id) == "PROCESSING_COMPLETED"
            )
            if completed:
                sink_completions.add(unit_id)
            _track_bounds(result, _as_int(record.get("created_at_ms")))
            result.rows.append(
                {
                    "run_id": run_id,
                    "unit_id": unit_id,
                    "created_at_ms": _as_int(record.get("created_at_ms")),
                    "vehicle_model": _as_text(record.get("vehicle_model")),
                    "supplier_batch": _as_text(record.get("supplier_batch")),
                    "first_seen_ms": entry.get("first_seen_ms"),
                    "last_seen_ms": entry.get("last_seen_ms"),
                    "last_station_id": last_station,
                    "completed": 1 if completed else 0,
                    "inspection_result": inspections.get(unit_id),
                }
            )
    return result


def _last_station_label(station_events: list[dict[str, Any]]) -> str | None:
    """The highest station label seen in the event stream -- the observable line end."""
    labels = {
        event["station_id"]
        for event in station_events
        if isinstance(event.get("station_id"), str) and event["station_id"].startswith("S")
    }
    if not labels:
        return None
    try:
        return max(labels, key=lambda label: int(label[1:]))
    except ValueError:
        return None


def _read_inspection_results(run_dir: Path) -> dict[str, str]:
    """Ground-truth inspection outcomes per unit, if the run recorded any.

    A unit that fails any inspection is FAIL regardless of later passes. These are real
    observed outcomes and are the only defect ground truth the repository produces -- the
    reason ``prediction_outcomes`` can eventually be populated honestly.
    """
    path = run_dir / "inspection_results.csv"
    results: dict[str, str] = {}
    if not path.is_file():
        return results
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            for record in csv.DictReader(stream):
                unit = _as_text(record.get("unit_id"))
                outcome = _as_text(record.get("result"))
                if unit is None or outcome is None:
                    continue
                if results.get(unit) == "FAIL":
                    continue
                results[unit] = outcome
    except OSError as error:
        logger.warning("could not read inspection results in %s: %s", run_dir, error)
    return results


# -- observability -----------------------------------------------------------------------

#: Which artifact supplies which observability channel, and how each is labelled.
_EVIDENCE_SOURCES: tuple[tuple[str, str, str, str], ...] = (
    ("sensor_readings.csv", "SENSOR", "sensor_type", "station_id"),
    ("manual_checks.csv", "MANUAL", "check_type", "station_id"),
    ("checkpoint_events.csv", "CHECKPOINT", "checkpoint_id", "station_id"),
    ("inspection_results.csv", "INSPECTION", None, "station_id"),
)


def read_sensor_observations(run_dir: str | Path, run_id: str) -> StreamResult:
    """Summarise what was actually observed at each station during one run.

    The problem statement's central constraint is uneven instrumentation, so the read
    model needs to distinguish a richly instrumented station from a manual-only one and
    from an unobserved one. That distinction is available as *evidence that arrived*, and
    that is what is counted here: channel, count, and the span it covered.

    ``sensor_readings.csv`` is ~234k rows per run, so it is streamed and aggregated
    rather than stored -- individual readings answer no dashboard question.
    """
    run_dir = Path(run_dir)
    result = StreamResult(path=str(run_dir))
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}

    for filename, kind, channel_field, station_field in _EVIDENCE_SOURCES:
        path = run_dir / filename
        if not path.is_file():
            continue
        result.exists = True
        try:
            with path.open(encoding="utf-8", newline="") as stream:
                for record in csv.DictReader(stream):
                    station = _as_text(record.get(station_field))
                    if station is None:
                        continue
                    channel = (
                        _as_text(record.get(channel_field)) or kind
                        if channel_field
                        else kind
                    )
                    timestamp = _as_int(record.get("timestamp_ms"))
                    key = (station, kind, channel)
                    bucket = buckets.get(key)
                    if bucket is None:
                        bucket = buckets[key] = {
                            "run_id": run_id,
                            "station_id": station,
                            "channel_kind": kind,
                            "channel": channel,
                            "observation_count": 0,
                            "first_timestamp_ms": timestamp,
                            "last_timestamp_ms": timestamp,
                        }
                    bucket["observation_count"] += 1
                    if timestamp is not None:
                        if bucket["first_timestamp_ms"] is None or timestamp < bucket["first_timestamp_ms"]:
                            bucket["first_timestamp_ms"] = timestamp
                        if bucket["last_timestamp_ms"] is None or timestamp > bucket["last_timestamp_ms"]:
                            bucket["last_timestamp_ms"] = timestamp
                        _track_bounds(result, timestamp)
        except OSError as error:
            logger.warning("could not read %s: %s", path, error)

    result.rows = [buckets[key] for key in sorted(buckets)]
    return result


# -- factory topology ----------------------------------------------------------------------


def read_run_topology(
    run_dir: str | Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Recover the topology a completed run actually executed against.

    ``stations.csv`` and ``dz.csv`` are written by the simulator *for that run*, which
    makes them a better topology source than ``factory.json``: the configuration file is
    edited between runs, and this repository's own corridor was shortened from four
    stations to three on this branch. Reading the run's own copies means an old run keeps
    rendering as it really ran instead of being redrawn against today's configuration.

    They also settle the station-label question directly. ``dz.csv`` names the corridor
    by runtime label (``S12`` to ``S14``), so no ``id + 1`` arithmetic is involved and the
    off-by-one that would shift the whole corridor cannot happen.

    Returns ``(station_rows, dark_zone_rows, fingerprint)`` with the fingerprint left out
    of the rows -- the caller stamps it, since it identifies the topology, not the run.
    """
    run_dir = Path(run_dir)
    stations_path = run_dir / "stations.csv"
    if not stations_path.is_file():
        return [], [], None

    raw_stations: list[dict[str, Any]] = []
    try:
        with stations_path.open(encoding="utf-8", newline="") as stream:
            for record in csv.DictReader(stream):
                station_id = _as_text(record.get("station_id"))
                if station_id is None:
                    continue
                raw_stations.append(record)
    except OSError as error:
        logger.warning("could not read %s: %s", stations_path, error)
        return [], [], None

    if not raw_stations:
        return [], [], None

    zones: list[dict[str, Any]] = []
    dark_labels: dict[str, str] = {}
    dz_path = run_dir / "dz.csv"
    if dz_path.is_file():
        try:
            with dz_path.open(encoding="utf-8", newline="") as stream:
                for record in csv.DictReader(stream):
                    zone_id = _as_text(record.get("dark_zone_id"))
                    start = _as_text(record.get("start_station_id"))
                    end = _as_text(record.get("end_station_id"))
                    if not zone_id or not start or not end:
                        continue
                    zones.append(
                        {
                            "dark_zone_id": zone_id,
                            "name": _as_text(record.get("name")),
                            "start_station_id": start,
                            "end_station_id": end,
                            "sensor_telemetry": _flag(record.get("sensor_telemetry")),
                            "manual_checks": _flag(record.get("manual_checks")),
                            "checkpoints": _flag(record.get("checkpoints")),
                        }
                    )
        except OSError as error:
            logger.warning("could not read %s: %s", dz_path, error)

    labels = [str(record["station_id"]) for record in raw_stations]
    index_of = {label: position for position, label in enumerate(labels)}
    for zone in zones:
        start, end = index_of.get(zone["start_station_id"]), index_of.get(zone["end_station_id"])
        if start is None or end is None:
            continue
        for position in range(start, end + 1):
            dark_labels[labels[position]] = zone["dark_zone_id"]

    station_rows: list[dict[str, Any]] = []
    for position, record in enumerate(raw_stations):
        label = labels[position]
        base_cycle = _as_int(record.get("base_cycle_time_ms")) or 0
        std = _as_int(record.get("cycle_time_std_ms")) or 0
        station_rows.append(
            {
                "station_id": label,
                "station_index": position,
                "name": _as_text(record.get("name")) or label,
                "archetype": _as_text(record.get("archetype")) or "AUTOMATED",
                "mean_cycle_time_ms": base_cycle,
                "cycle_time_cv": (std / base_cycle) if base_cycle else 0.0,
                "buffer_capacity": _as_int(record.get("buffer_capacity")) or 0,
                "sensor_coverage": _as_text(record.get("sensor_coverage")) or "NONE",
                # stations.csv carries no source/sink flags; the line's endpoints are its
                # first and last stations, which is how the simulator defines them.
                "is_source": 1 if position == 0 else 0,
                "is_sink": 1 if position == len(raw_stations) - 1 else 0,
                "is_dark": 1 if label in dark_labels else 0,
                "dark_zone_id": dark_labels.get(label),
                "upstream_station_id": labels[position - 1] if position else None,
                "downstream_station_id": (
                    labels[position + 1] if position + 1 < len(labels) else None
                ),
            }
        )

    # The fingerprint identifies this *topology*, so runs sharing a line share stations
    # rows and a factory.json edit between runs produces a new, separate topology.
    payload = json.dumps({"stations": station_rows, "zones": zones}, sort_keys=True)
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return station_rows, zones, fingerprint


def build_topology_rows(
    factory: dict[str, Any], fingerprint: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project a ``factory.json`` payload into ``stations`` and ``dark_zones`` rows.

    Upstream/downstream links come from the line sequence, which for this simulator is
    the station id order. Representing them explicitly means the Live Factory reads a
    topology instead of assuming one, so a factory with a different station count or
    naming renders without any UI change.
    """
    from dashboard.domain.station import Station

    stations = Station.all_from_factory(factory)
    labels = [station.runtime_label for station in stations]

    station_rows: list[dict[str, Any]] = []
    for position, station in enumerate(stations):
        station_rows.append(
            {
                "factory_fingerprint": fingerprint,
                "station_id": station.runtime_label,
                "station_index": station.id,
                "name": station.name,
                "archetype": station.archetype,
                "mean_cycle_time_ms": station.mean_cycle_time_ms,
                "cycle_time_cv": station.cycle_time_cv,
                "buffer_capacity": station.buffer_capacity,
                "sensor_coverage": station.sensor_coverage,
                "is_source": 1 if station.is_source else 0,
                "is_sink": 1 if station.is_sink else 0,
                "is_dark": 1 if station.is_dark else 0,
                "dark_zone_id": station.dark_zone_id,
                "upstream_station_id": labels[position - 1] if position else None,
                "downstream_station_id": (
                    labels[position + 1] if position + 1 < len(labels) else None
                ),
            }
        )

    from dashboard.domain.station import station_runtime_label

    zone_rows: list[dict[str, Any]] = []
    for zone in factory.get("darkZones") or []:
        if not isinstance(zone, dict):
            continue
        start, end = zone.get("startStationId"), zone.get("endStationId")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        observability = zone.get("observability") or {}
        zone_rows.append(
            {
                "factory_fingerprint": fingerprint,
                "dark_zone_id": str(zone.get("id") or f"DZ_{start}_{end}"),
                "name": _as_text(zone.get("name")),
                "start_station_id": station_runtime_label(start),
                "end_station_id": station_runtime_label(end),
                "sensor_telemetry": _flag(observability.get("sensorTelemetry")),
                "manual_checks": _flag(observability.get("manualChecks")),
                "checkpoints": _flag(observability.get("checkpoints")),
            }
        )
    return station_rows, zone_rows
