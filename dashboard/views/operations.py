"""Operational Streamlit views over the existing analytics read model."""
from __future__ import annotations

from html import escape
import json
from typing import Any

import streamlit as st


def _service(context):
    if context.analytics is None:
        st.warning("Dashboard analytics are unavailable; the database could not be opened.")
        return None
    return context.analytics


def _run(context, service):
    selected = st.session_state.get("selected_run_id")
    return (service.get_run(selected) if selected else None) or service.latest_analysed_run()


def _require_run(context, title):
    service = _service(context)
    if service is None:
        return None, None
    run = _run(context, service)
    if run is None:
        st.header(title)
        st.info("No analysed run is available. Run Factory or rebuild dashboard history from completed artifacts.")
        return None, None
    return service, run


def _pct(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def _duration(value: Any) -> str:
    if value is None:
        return "—"
    seconds = int(value) // 1000
    return f"{seconds // 60}m {seconds % 60}s"


def _driver(row: dict[str, Any] | None) -> str:
    if not row:
        return "No predictive driver available"
    direct = row.get("top_driver") or row.get("feature") or row.get("label")
    if direct:
        return str(direct)
    for key in ("risk_drivers_json", "top_drivers_json"):
        try:
            drivers = json.loads(row.get(key) or "[]")
        except (TypeError, ValueError):
            continue
        if isinstance(drivers, list) and drivers and isinstance(drivers[0], dict):
            return str(drivers[0].get("feature") or drivers[0].get("label") or "Predictive contribution")
    return "No predictive driver available"


def render_overview(context) -> None:
    service, run = _require_run(context, "Overview")
    if service is None:
        return
    st.header("Overview")
    metrics = service.get_run_metrics(run.run_id)
    if not metrics:
        st.info("This run is recorded but has no analytical projection yet. Rebuild from artifacts to populate it.")
        return
    st.caption(f"Production day {run.production_day} · `{run.run_id}`")
    cols = st.columns(4)
    cols[0].metric("Throughput", "—" if metrics["throughput_per_hour"] is None else f"{metrics['throughput_per_hour']:.1f}/h")
    cols[1].metric("Cycle time", _duration(metrics.get("avg_lead_time_ms")))
    cols[2].metric("Peak WIP", metrics.get("peak_wip") if metrics.get("peak_wip") is not None else "—")
    cols[3].metric("Runtime health", metrics.get("health_status") or "Unknown")
    cols = st.columns(3)
    cols[0].metric("Bottleneck alerts", metrics.get("bottleneck_alert_count", 0))
    cols[1].metric("Defect alerts", metrics.get("defect_alert_count", 0))
    cols[2].metric("Observed coverage", _pct(metrics.get("observability_coverage")))
    ranked = sorted(service.get_station_metrics(run.run_id), key=lambda row: row.get("peak_probability") or -1, reverse=True)[:8]
    if ranked:
        st.subheader("Highest station risk")
        st.bar_chart({row["station_id"]: (row.get("peak_probability") or 0) * 100 for row in ranked})


def render_live_twin(context) -> None:
    service, run = _require_run(context, "Live Factory")
    if service is None:
        return
    st.header("Live Factory")
    first, last = service.get_run_time_bounds(run.run_id)
    at = st.slider("Simulator time (ms)", first, last, last, key="live_time") if last > first else last
    state = service.get_live_state(run.run_id, at)
    st.caption(f"Run `{state.run_id}` · production day {state.production_day or '—'} · simulator {state.sim_time_ms:,} ms · runtime {state.health_status or 'Unknown'}")
    if state.health_status and state.health_status != "PASS":
        st.warning(f"Runtime health is {state.health_status}; data may be incomplete.")
    for notice in state.notices:
        st.info(notice)
    if state.is_empty:
        st.info("No topology was ingested for this run. Rebuild dashboard history from artifacts.")
        return
    if not state.bottleneck_stream_available:
        st.warning("Bottleneck stream is unavailable; no-signal is not zero risk.")
    if not state.defect_stream_available:
        st.warning("Defect stream is unavailable; unit risk is not available.")
    cards = []
    for station in state.stations:
        queue = station.queue
        beads = "".join("●" for _ in range(min(queue.occupancy or 0, 12))) if queue and queue.observable else "?"
        cards.append(
            f"<div style='display:inline-block;white-space:normal;vertical-align:top;width:178px;min-height:140px;margin:6px;padding:10px;border:2px solid #64748b;border-radius:8px'>"
            f"<b>{escape(station.station_id)}</b><br><small>{escape(station.name)}</small><hr style='margin:6px 0'>"
            f"Risk: <b>{escape(station.risk)}</b> {_pct(station.bottleneck_probability)}<br>"
            f"Queue: {beads} {queue.label if queue else '—'}<br>Utilization: {_pct(station.utilization)}<br><small>{station.observability}</small></div>"
        )
    st.markdown("**Line view** — scroll horizontally to inspect all stations.")
    st.markdown("<div style='overflow-x:auto;white-space:nowrap;padding-bottom:8px'>" + "".join(cards) + "</div>", unsafe_allow_html=True)
    station_id = st.selectbox("Station details", [station.station_id for station in state.stations])
    detail = service.get_station_detail(run.run_id, station_id, state.sim_time_ms)
    metric = detail["metrics"] or {}
    station = detail["station"]
    with st.expander(f"{station_id} detail", expanded=True):
        st.write(f"**{station.name if station else station_id}** · Peak risk {_pct(metric.get('peak_probability'))} · Average {_pct(metric.get('avg_probability'))}")
        st.write(f"Queue {metric.get('last_queue', '—')} / {metric.get('buffer_capacity', '—')} · Utilization {_pct(metric.get('utilization'))} · Confidence {_pct(metric.get('mean_confidence'))}")
        st.write(f"Recent alerts: {len(detail['alerts'])}")
    units = state.units_on_line
    if units:
        unit_id = st.selectbox("Unit details", [unit.unit_id for unit in units])
        unit = service.get_unit_detail(run.run_id, unit_id, state.sim_time_ms)
        latest = unit["latest"] or {}
        with st.expander(f"{unit_id} detail"):
            st.write(f"Current station: {latest.get('station_id') or (unit['metrics'] or {}).get('last_station_id') or '—'} · Defect risk {_pct(latest.get('probability'))} · Confidence {_pct(latest.get('state_confidence'))}")
            st.write(f"Recent alerts: {len(unit['alerts'])}")
            st.caption("Predictive contributions are associated signals, not causal root-cause conclusions.")


def render_bottlenecks(context) -> None:
    service, run = _require_run(context, "Bottleneck Analytics")
    if service is None:
        return
    st.header("Bottleneck Analytics")
    rows = service.get_station_metrics(run.run_id)
    if not rows or not any(row.get("prediction_count") for row in rows):
        st.info("No bottleneck prediction stream is available for this run.")
        return
    table = [{"Station": r["station_id"], "Current Risk": _pct(r.get("last_probability")), "Peak Risk": _pct(r.get("peak_probability")), "Average Risk": _pct(r.get("avg_probability")), "Alerts": r.get("alert_count", 0), "Time at Risk": _duration(r.get("time_above_threshold_ms")), "Confidence": _pct(r.get("mean_confidence")), "Queue": f"{r.get('last_queue') if r.get('last_queue') is not None else '—'} / {r.get('buffer_capacity') if r.get('buffer_capacity') is not None else '—'}"} for r in rows]
    st.dataframe(table, hide_index=True, use_container_width=True)
    selected = st.selectbox("Risk trend for station", [r["station_id"] for r in rows])
    history = service.get_bottleneck_history(run.run_id, selected)
    if history:
        st.line_chart({"Risk %": [float(row.get("probability") or 0) * 100 for row in history]})
        st.write(f"Top predictive driver: {_driver(history[-1])}")


def render_defects(context) -> None:
    service, run = _require_run(context, "Defect Analytics")
    if service is None:
        return
    st.header("Defect Analytics")
    rows = service.get_unit_metrics(run.run_id, limit=100)
    if not rows or not any(row.get("prediction_count") for row in rows):
        st.info("No defect prediction stream is available for this run.")
        return
    st.dataframe([{"Unit": r["unit_id"], "Current Risk": _pct(r.get("last_probability")), "Peak Risk": _pct(r.get("peak_probability")), "Confidence": _pct(r.get("mean_confidence")), "Current Station": r.get("last_station_id") or "—", "Warnings": r.get("warning_count", 0)} for r in rows], hide_index=True, use_container_width=True)
    selected = st.selectbox("Unit risk detail", [r["unit_id"] for r in rows])
    detail = service.get_unit_detail(run.run_id, selected)
    if detail["history"]:
        st.line_chart({"Defect risk %": [float(row.get("probability") or 0) * 100 for row in detail["history"]]})
    st.caption("Defect risk is a unit/quality signal, distinct from station/flow bottleneck risk. Drivers are associated signals, not causes.")


def render_sensor_coverage(context) -> None:
    service, run = _require_run(context, "Sensor Analytics")
    if service is None:
        return
    st.header("Sensor Analytics")
    coverage = service.get_sensor_coverage(run.run_id)
    cols = st.columns(4)
    cols[0].metric("Stations", coverage["station_count"]); cols[1].metric("Instrumented", coverage["instrumented_count"]); cols[2].metric("Manual-only", coverage["manual_only_count"]); cols[3].metric("Unobserved", coverage["unobserved_count"])
    st.caption("Configured sensor coverage is distinct from observed state. DARK is a low-observability corridor, not a sensor reading.")
    st.dataframe([{"Station": item.station_id, "Name": item.name, "Configured coverage": item.sensor_coverage, "Observed state": item.observability, "Channels": ", ".join(item.channel_kinds) or "None", "Prediction confidence": _pct(item.mean_confidence)} for item in coverage["stations"]], hide_index=True, use_container_width=True)


def render_leadership(context) -> None:
    service, run = _require_run(context, "Business Case")
    if service is None:
        return
    st.header("Business Case")
    metrics = service.get_run_metrics(run.run_id) or {}
    cols = st.columns(4)
    cols[0].metric("Throughput", "—" if metrics.get("throughput_per_hour") is None else f"{metrics['throughput_per_hour']:.1f}/h"); cols[1].metric("Bottleneck events", metrics.get("bottleneck_alert_count", 0)); cols[2].metric("Defect-risk events", metrics.get("defect_alert_count", 0)); cols[3].metric("Observed coverage", _pct(metrics.get("observability_coverage")))
    st.subheader("Prediction trust")
    st.info("Validation pending real outcomes. This prototype does not claim precision, recall, ROI, savings, or causal root causes.")
    st.subheader("Deployment readiness")
    st.write("Legacy-compatible configuration · isolated runtime execution · artifact-based integration · uneven-observability support · analytical read model")
    st.subheader("Scale path")
    st.markdown("Pilot Line  →  Plant  →  Multi-line  →  Multi-site")


def render_runtime_health(context) -> None:
    service, run = _require_run(context, "Runtime Health")
    if service is None:
        return
    st.header("Runtime Health")
    summary = service.get_run_summary(run.run_id)
    metrics = (summary.metrics if summary else None) or {}
    st.metric("Current status", metrics.get("health_status") or "Unknown")
    if metrics.get("health_status") and metrics["health_status"] != "PASS":
        st.warning("Runtime is degraded or incomplete; use Run History to inspect recorded artifacts.")
    else:
        st.success("No degraded runtime status was recorded for this run.")
    st.dataframe((summary.ingest_sources if summary else []), hide_index=True, use_container_width=True)


def render_configuration(context) -> None:
    st.header("Configuration")
    st.metric("Factory status", context.factory.status)
    st.caption(f"Factory configuration: `{context.config.factory_path}`")
    st.metric("Stations", context.factory.station_count)
    st.metric("DARK zones", context.factory.dark_zone_count)
    if context.factory.validation.warnings:
        for warning in context.factory.validation.warnings:
            st.warning(warning)
