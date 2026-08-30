"""Derived run, station and unit metrics.

Everything here is computed once at ingestion time from rows already in the read model,
then stored. No metric is recomputed while a page renders -- that is the whole point of
the exercise: the previous dashboard re-parsed up to 71 MB of JSONL on every Streamlit
rerun to answer questions this module answers with an indexed lookup.

Every formula below is stated in the docstring of the function that implements it, and
each one uses only fields the repository actually produces. Where a quantity is not
observable -- a DARK station's buffer occupancy, for instance -- the metric is left null
rather than filled in with a guess.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Sequence

from dashboard.analytics.alerts import BOTTLENECK, DEFECT, SEVERITY_CRITICAL, critical_cut

#: Buffer-carrying event types, mirroring ``dashboard.ingestion.normalizers``.
BUFFER_EVENTS = frozenset({"UNIT_ARRIVED", "PROCESSING_STARTED", "PROCESSING_COMPLETED"})

#: How a station's state was known during a run.
OBSERVABILITY_DIRECT = "DIRECT"
OBSERVABILITY_RECONSTRUCTED = "RECONSTRUCTED"
OBSERVABILITY_UNOBSERVED = "UNOBSERVED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    """Nearest-rank percentile. Small sample sizes make interpolation false precision."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(fraction * (len(ordered) - 1))))
    return ordered[index]


# -- station metrics -----------------------------------------------------------------------


def compute_station_metrics(
    run_id: str,
    stations: Sequence[dict[str, Any]],
    bottleneck_rows: Sequence[dict[str, Any]],
    queue_rows: Sequence[dict[str, Any]],
    alerts: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One row per configured station, whether or not anything was observed there.

    Producing a row for every station -- including the ones the model never scored -- is
    deliberate. A station with no predictions must read as *no signal*, never as 0% risk,
    and it can only do that if the read model distinguishes "measured zero" from "never
    measured". In this repository ``S14`` sits inside the DARK corridor and receives no
    bottleneck predictions at all; that has to stay visible.

    Formulas
    --------
    ``avg_probability``   arithmetic mean over the station's predictions. Predictions are
                          event-triggered rather than periodic, so a time weighting would
                          imply a sampling regularity that does not exist.
    ``time_above_threshold_ms``
                          sum of the gaps between consecutive predictions for this station
                          where the *earlier* prediction carried ``warning``. The span
                          after the final prediction is not attributed to anything, since
                          there is no evidence covering it.
    ``avg_queue``         time-weighted mean of ``occupancy``, each reading held until the
                          next one for that station.
    ``utilization``       total ``cycle_time_ms`` recorded on ``PROCESSING_COMPLETED``
                          divided by the station's observed span, clamped to 1.0.
    ``critical_count``    predictions whose probability reached the severity cut defined
                          in :mod:`dashboard.analytics.alerts`.
    """
    computed_at = _now()

    by_station_predictions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in bottleneck_rows:
        by_station_predictions[row["station_id"]].append(row)

    by_station_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in queue_rows:
        by_station_events[row["station_id"]].append(row)

    alert_counts: dict[str, int] = defaultdict(int)
    for alert in alerts:
        if alert["alert_type"] == BOTTLENECK:
            alert_counts[alert["entity_id"]] += 1

    metrics: list[dict[str, Any]] = []
    for station in stations:
        station_id = station["station_id"]
        predictions = sorted(
            by_station_predictions.get(station_id, []),
            key=lambda item: (item["timestamp_ms"], item["source_seq"]),
        )
        events = sorted(
            by_station_events.get(station_id, []),
            key=lambda item: (item["timestamp_ms"], item["source_seq"]),
        )

        probabilities = [p["probability"] for p in predictions if p["probability"] is not None]
        confidences = [
            p["state_confidence"] for p in predictions if p["state_confidence"] is not None
        ]
        threshold = next(
            (p["decision_threshold"] for p in reversed(predictions) if p["decision_threshold"]),
            None,
        )
        cut = critical_cut(threshold)

        time_above = 0
        for earlier, later in zip(predictions, predictions[1:]):
            if earlier["warning"]:
                time_above += max(0, later["timestamp_ms"] - earlier["timestamp_ms"])

        buffer_readings = [
            event for event in events
            if event["event_type"] in BUFFER_EVENTS and event["occupancy"] is not None
        ]
        avg_queue = None
        peak_queue = None
        last_queue = None
        if buffer_readings:
            peak_queue = max(event["occupancy"] for event in buffer_readings)
            last_queue = buffer_readings[-1]["occupancy"]
            weighted = 0.0
            span = 0
            for earlier, later in zip(buffer_readings, buffer_readings[1:]):
                width = max(0, later["timestamp_ms"] - earlier["timestamp_ms"])
                weighted += earlier["occupancy"] * width
                span += width
            avg_queue = weighted / span if span else float(buffer_readings[-1]["occupancy"])

        busy_ms = sum(
            event["cycle_time_ms"] or 0
            for event in events
            if event["event_type"] == "PROCESSING_COMPLETED"
        )
        observed_span = (
            events[-1]["timestamp_ms"] - events[0]["timestamp_ms"] if len(events) > 1 else 0
        )
        utilization = min(1.0, busy_ms / observed_span) if observed_span > 0 else None

        if buffer_readings:
            observability = OBSERVABILITY_DIRECT
        elif predictions:
            observability = OBSERVABILITY_RECONSTRUCTED
        else:
            observability = OBSERVABILITY_UNOBSERVED

        metrics.append(
            {
                "run_id": run_id,
                "station_id": station_id,
                "station_index": station["station_index"],
                "prediction_count": len(predictions),
                "last_probability": predictions[-1]["probability"] if predictions else None,
                "avg_probability": _mean(probabilities),
                "peak_probability": max(probabilities) if probabilities else None,
                "decision_threshold": threshold,
                "time_above_threshold_ms": time_above,
                "observed_span_ms": observed_span,
                "warning_count": sum(1 for p in predictions if p["warning"]),
                "critical_count": (
                    sum(1 for p in probabilities if cut is not None and p >= cut)
                    if cut is not None
                    else 0
                ),
                "alert_count": alert_counts.get(station_id, 0),
                "mean_confidence": _mean(confidences),
                "min_confidence": min(confidences) if confidences else None,
                "avg_queue": avg_queue,
                "peak_queue": peak_queue,
                "last_queue": last_queue,
                "buffer_capacity": station["buffer_capacity"],
                "utilization": utilization,
                "busy_ms": busy_ms,
                "units_processed": sum(
                    1 for event in events if event["event_type"] == "PROCESSING_COMPLETED"
                ),
                "observability": observability,
                "computed_at": computed_at,
            }
        )
    return metrics


# -- unit metrics ---------------------------------------------------------------------------


def compute_unit_metrics(
    run_id: str,
    units: Sequence[dict[str, Any]],
    defect_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One row per unit that has at least one defect prediction or a manifest entry.

    ``largest_increase`` records the single biggest jump in defect probability between
    consecutive predictions for the unit, together with the station the unit was at when
    it happened. It is a *contribution* signal, not a cause: the model reports that risk
    rose most sharply there, which is a place to look, not a verdict. Every surface that
    shows it must say so.
    """
    computed_at = _now()

    by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in defect_rows:
        by_unit[row["unit_id"]].append(row)

    manifest = {unit["unit_id"]: unit for unit in units}
    unit_ids = sorted(set(by_unit) | set(manifest))

    metrics: list[dict[str, Any]] = []
    for unit_id in unit_ids:
        predictions = sorted(
            by_unit.get(unit_id, []),
            key=lambda item: (item["timestamp_ms"], item["source_seq"]),
        )
        record = manifest.get(unit_id, {})

        probabilities = [p["probability"] for p in predictions if p["probability"] is not None]
        confidences = [
            p["state_confidence"] for p in predictions if p["state_confidence"] is not None
        ]

        peak = max(probabilities) if probabilities else None
        peak_station = None
        if peak is not None:
            peak_station = next(
                (p["station_id"] for p in predictions if p["probability"] == peak), None
            )

        largest_increase = None
        largest_increase_station = None
        for earlier, later in zip(predictions, predictions[1:]):
            if earlier["probability"] is None or later["probability"] is None:
                continue
            delta = later["probability"] - earlier["probability"]
            if largest_increase is None or delta > largest_increase:
                largest_increase = delta
                largest_increase_station = later["station_id"]

        first_warning = next(
            (p["timestamp_ms"] for p in predictions if p["warning"]), None
        )

        created = record.get("created_at_ms")
        last_seen = record.get("last_seen_ms")
        lead_time = (
            last_seen - created
            if record.get("completed") and created is not None and last_seen is not None
            else None
        )

        metrics.append(
            {
                "run_id": run_id,
                "unit_id": unit_id,
                "prediction_count": len(predictions),
                "last_probability": predictions[-1]["probability"] if predictions else None,
                "avg_probability": _mean(probabilities),
                "peak_probability": peak,
                "peak_at_station_id": peak_station,
                "decision_threshold": next(
                    (p["decision_threshold"] for p in reversed(predictions) if p["decision_threshold"]),
                    None,
                ),
                "warning_count": sum(1 for p in predictions if p["warning"]),
                "first_warning_ms": first_warning,
                "mean_confidence": _mean(confidences),
                "last_station_id": (
                    predictions[-1]["station_id"] if predictions else record.get("last_station_id")
                ),
                "last_timestamp_ms": (
                    predictions[-1]["timestamp_ms"] if predictions else record.get("last_seen_ms")
                ),
                "largest_increase": largest_increase,
                "largest_increase_station_id": largest_increase_station,
                "lead_time_ms": lead_time,
                "inspection_result": record.get("inspection_result"),
                "computed_at": computed_at,
            }
        )
    return metrics


# -- run metrics -----------------------------------------------------------------------------


def compute_wip_profile(units: Sequence[dict[str, Any]], run_end_ms: int | None) -> tuple[float | None, int | None]:
    """Time-weighted average and peak work-in-progress.

    A unit contributes to WIP from its first observed station event until its last. A
    unit still on the line when the run ended contributes until ``run_end_ms``, which is
    the honest reading: it had not left.
    """
    transitions: list[tuple[int, int]] = []
    for unit in units:
        start = unit.get("first_seen_ms")
        if start is None:
            continue
        end = unit.get("last_seen_ms") if unit.get("completed") else run_end_ms
        if end is None:
            end = unit.get("last_seen_ms")
        if end is None or end < start:
            continue
        transitions.append((start, 1))
        transitions.append((end, -1))

    if not transitions:
        return None, None

    transitions.sort()
    current = 0
    peak = 0
    weighted = 0.0
    span = 0
    previous_ms = transitions[0][0]
    for timestamp, delta in transitions:
        width = timestamp - previous_ms
        if width > 0:
            weighted += current * width
            span += width
        current += delta
        peak = max(peak, current)
        previous_ms = timestamp
    average = weighted / span if span else float(peak)
    return average, peak


def compute_run_metrics(
    run_id: str,
    *,
    stations: Sequence[dict[str, Any]],
    station_metrics: Sequence[dict[str, Any]],
    units: Sequence[dict[str, Any]],
    bottleneck_count: int,
    defect_count: int,
    alerts: Sequence[dict[str, Any]],
    first_timestamp_ms: int | None,
    last_timestamp_ms: int | None,
    simulated_duration_ms: int | None,
    health_status: str | None,
    mean_state_confidence: float | None,
) -> dict[str, Any]:
    """Aggregate one run.

    ``throughput_per_hour`` uses the simulated duration the run actually covered, not
    wall-clock time: the coordinated replay is unpaced, so wall clock says nothing about
    the line.

    ``observability_coverage`` is the share of configured stations whose buffer state was
    directly observed during the run. It is the metric the problem statement's uneven
    instrumentation constraint turns into, and it is measured, not configured -- a
    station can be declared instrumented and still emit nothing.
    """
    completed = [unit for unit in units if unit.get("completed")]
    lead_times = [
        float(unit["last_seen_ms"] - unit["created_at_ms"])
        for unit in completed
        if unit.get("last_seen_ms") is not None and unit.get("created_at_ms") is not None
    ]

    duration = simulated_duration_ms
    if not duration and first_timestamp_ms is not None and last_timestamp_ms is not None:
        duration = last_timestamp_ms - first_timestamp_ms

    average_wip, peak_wip = compute_wip_profile(units, last_timestamp_ms)

    direct = sum(
        1 for metric in station_metrics if metric["observability"] == OBSERVABILITY_DIRECT
    )
    predicted = sum(1 for metric in station_metrics if metric["prediction_count"] > 0)

    return {
        "run_id": run_id,
        "simulated_duration_ms": duration,
        "first_timestamp_ms": first_timestamp_ms,
        "last_timestamp_ms": last_timestamp_ms,
        "units_created": len(units),
        "units_completed": len(completed),
        "throughput_per_hour": (
            len(completed) / (duration / 3_600_000) if duration else None
        ),
        "avg_lead_time_ms": _mean(lead_times),
        "p95_lead_time_ms": _percentile(lead_times, 0.95),
        "avg_wip": average_wip,
        "peak_wip": peak_wip,
        "bottleneck_prediction_count": bottleneck_count,
        "defect_prediction_count": defect_count,
        "bottleneck_alert_count": sum(1 for a in alerts if a["alert_type"] == BOTTLENECK),
        "defect_alert_count": sum(1 for a in alerts if a["alert_type"] == DEFECT),
        "critical_alert_count": sum(1 for a in alerts if a["severity"] == SEVERITY_CRITICAL),
        "station_count": len(stations),
        "observed_station_count": direct,
        "predicted_station_count": predicted,
        "dark_station_count": sum(1 for station in stations if station["is_dark"]),
        "observability_coverage": direct / len(stations) if stations else None,
        "mean_state_confidence": mean_state_confidence,
        "health_status": health_status,
        "computed_at": _now(),
    }
