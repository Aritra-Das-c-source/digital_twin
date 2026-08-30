"""Alert lifecycle derivation.

An alert is an *episode*, not a row. The prediction streams emit a record per triggering
event -- 4,668 bottleneck records in a single eight-hour day in this repository, 1,244 of
them carrying ``warning = true``. Presenting 1,244 alerts would be meaningless; those
records describe a much smaller number of periods during which a station was actually in
trouble. This module collapses the former into the latter.

Lifecycle
---------

For each entity (a station for bottlenecks, a unit for defects) the entity's own records
are read in ``(timestamp_ms, source_seq)`` order and run through one state machine::

    closed  --record with warning=1-->  OPEN      (alert starts at that timestamp)
    OPEN    --record with warning=1-->  OPEN      (same alert continues; nothing new)
    OPEN    --record with warning=0-->  RESOLVED  (alert ends at that timestamp)
    OPEN    --stream ends------------>  OPEN      (unresolved when the run ended)

Consequences worth stating explicitly, because they are the behaviour that makes counts
trustworthy:

* Repeated warning ticks -- including several at the *same* timestamp -- extend one
  alert. They never create a second one.
* A resolved alert that re-fires later is a new, separate alert. Recurrence is visible
  as a count, which is what "which station alerts repeatedly?" needs.
* An alert still open when the stream ends keeps ``status = OPEN`` and a null
  ``ended_at_ms``. Its duration is measured to the entity's last observation, and the
  fact that it never resolved is preserved rather than being rounded off.

Signal
------

``warning`` is the trigger on both streams and is taken verbatim from the record --
never recomputed from probability and threshold. For defects that matters: the runtime
deliberately suppresses ``warning`` once a vehicle reaches final inspection, so a defect
alert naturally closes when the unit arrives there. That is correct: there is no longer
an action for the floor to take.

Severity
--------

Severity is threshold-relative rather than a fixed probability, because each stream
carries its own calibrated ``decision_threshold`` (about 0.156 for bottlenecks and 0.142
for defects here) and a hard-coded cut would mean different things on each::

    CRITICAL  when peak probability >= threshold + (1 - threshold) / 2
    WARNING   otherwise

That is, an alert is CRITICAL once its peak sits halfway between the model's own
decision boundary and certainty.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Iterable

#: Alert stream kinds.
BOTTLENECK = "BOTTLENECK"
DEFECT = "DEFECT"

STATION = "STATION"
UNIT = "UNIT"

SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"

STATUS_OPEN = "OPEN"
STATUS_RESOLVED = "RESOLVED"


def critical_cut(threshold: float | None) -> float | None:
    """Probability at which an alert is treated as CRITICAL for a given threshold."""
    if threshold is None:
        return None
    return threshold + (1.0 - threshold) / 2.0


def _severity(peak: float | None, threshold: float | None) -> str:
    cut = critical_cut(threshold)
    if peak is not None and cut is not None and peak >= cut:
        return SEVERITY_CRITICAL
    return SEVERITY_WARNING


def _first_driver(drivers_json: str | None) -> str | None:
    if not drivers_json:
        return None
    try:
        drivers = json.loads(drivers_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(drivers, list) or not drivers:
        return None
    head = drivers[0]
    if not isinstance(head, dict):
        return None
    return head.get("label") or head.get("feature")


class _Episode:
    """One in-progress alert, accumulating statistics as records arrive."""

    __slots__ = (
        "started_at_ms", "last_ms", "peak", "opening", "closing", "threshold",
        "confidences", "observations", "top_driver", "peak_driver_seen",
    )

    def __init__(self, record: dict[str, Any], probability: float | None) -> None:
        self.started_at_ms = int(record["timestamp_ms"])
        self.last_ms = int(record["timestamp_ms"])
        self.peak = probability
        self.opening = probability
        self.closing = probability
        self.threshold = record.get("decision_threshold")
        self.confidences: list[float] = []
        self.observations = 0
        self.top_driver: str | None = None
        self.peak_driver_seen = False

    def observe(
        self, record: dict[str, Any], probability: float | None, driver: str | None
    ) -> None:
        self.last_ms = int(record["timestamp_ms"])
        self.closing = probability
        self.observations += 1
        confidence = record.get("state_confidence")
        if confidence is not None:
            self.confidences.append(float(confidence))
        if probability is not None and (self.peak is None or probability >= self.peak):
            self.peak = probability
            # The driver reported for the alert is the one explaining its worst moment.
            self.top_driver = driver
            self.peak_driver_seen = True
        elif not self.peak_driver_seen and driver:
            self.top_driver = driver


def _finish(
    episode: _Episode,
    *,
    run_id: str,
    alert_type: str,
    entity_type: str,
    entity_id: str,
    station_id: str | None,
    ended_at_ms: int | None,
) -> dict[str, Any]:
    resolved = ended_at_ms is not None
    end_for_duration = ended_at_ms if resolved else episode.last_ms
    confidences = episode.confidences
    return {
        "alert_id": f"{run_id}|{alert_type}|{entity_id}|{episode.started_at_ms}",
        "run_id": run_id,
        "alert_type": alert_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "station_id": station_id,
        "started_at_ms": episode.started_at_ms,
        "ended_at_ms": ended_at_ms,
        "duration_ms": max(0, int(end_for_duration) - episode.started_at_ms),
        "severity": _severity(episode.peak, episode.threshold),
        "status": STATUS_RESOLVED if resolved else STATUS_OPEN,
        "opening_probability": episode.opening,
        "peak_probability": episode.peak,
        "closing_probability": episode.closing,
        "decision_threshold": episode.threshold,
        "min_confidence": min(confidences) if confidences else None,
        "mean_confidence": sum(confidences) / len(confidences) if confidences else None,
        "observation_count": episode.observations,
        "top_driver": episode.top_driver,
    }


def _sessionize(
    records: Iterable[dict[str, Any]],
    *,
    run_id: str,
    alert_type: str,
    entity_type: str,
    entity_key: str,
    driver_key: str,
    station_of,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        entity = record.get(entity_key)
        if entity:
            grouped[str(entity)].append(record)

    alerts: list[dict[str, Any]] = []
    for entity_id, entity_records in grouped.items():
        entity_records.sort(key=lambda item: (item["timestamp_ms"], item["source_seq"]))
        episode: _Episode | None = None
        for record in entity_records:
            probability = record.get("probability")
            if record.get("warning"):
                if episode is None:
                    episode = _Episode(record, probability)
                episode.observe(record, probability, _first_driver(record.get(driver_key)))
            elif episode is not None:
                alerts.append(
                    _finish(
                        episode,
                        run_id=run_id,
                        alert_type=alert_type,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        station_id=station_of(record, entity_id),
                        ended_at_ms=int(record["timestamp_ms"]),
                    )
                )
                episode = None
        if episode is not None:
            alerts.append(
                _finish(
                    episode,
                    run_id=run_id,
                    alert_type=alert_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    station_id=station_of(entity_records[-1], entity_id),
                    ended_at_ms=None,
                )
            )

    alerts.sort(key=lambda alert: (alert["started_at_ms"], alert["entity_id"]))
    return alerts


def derive_bottleneck_alerts(
    records: Iterable[dict[str, Any]], run_id: str
) -> list[dict[str, Any]]:
    """Collapse bottleneck prediction rows into station alert episodes."""
    return _sessionize(
        records,
        run_id=run_id,
        alert_type=BOTTLENECK,
        entity_type=STATION,
        entity_key="station_id",
        driver_key="drivers_json",
        station_of=lambda record, entity_id: entity_id,
    )


def derive_defect_alerts(
    records: Iterable[dict[str, Any]], run_id: str
) -> list[dict[str, Any]]:
    """Collapse defect prediction rows into per-unit alert episodes.

    ``station_id`` on the resulting alert is where the unit was when the alert last had
    evidence -- useful for "where on the line did this unit's risk sit?", not a claim
    that the station caused the risk.
    """
    return _sessionize(
        records,
        run_id=run_id,
        alert_type=DEFECT,
        entity_type=UNIT,
        entity_key="unit_id",
        driver_key="risk_drivers_json",
        station_of=lambda record, entity_id: record.get("station_id"),
    )


def derive_alerts(
    bottleneck_records: Iterable[dict[str, Any]],
    defect_records: Iterable[dict[str, Any]],
    run_id: str,
) -> list[dict[str, Any]]:
    """Every alert for one run, both streams, ordered by start time."""
    alerts = derive_bottleneck_alerts(bottleneck_records, run_id)
    alerts += derive_defect_alerts(defect_records, run_id)
    alerts.sort(key=lambda alert: (alert["started_at_ms"], alert["alert_type"], alert["entity_id"]))
    return alerts
