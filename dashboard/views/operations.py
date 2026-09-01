"""Artifact-backed stakeholder and analysis views for the prototype dashboard."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import streamlit as st

from dashboard.domain.station import Station


def _records(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                if isinstance(item, dict): rows.append(item)
            except json.JSONDecodeError: pass
    except OSError: pass
    return rows


def _run(context):
    runs = context.run_history()
    chosen = st.session_state.get("selected_run_id")
    return next((r for r in runs if r.run_id == chosen), None) or (runs[0] if runs else None)


def _data(context):
    run = _run(context)
    if not run or not run.predictions_path: return run, [], []
    root = Path(run.predictions_path)
    return run, _records(root / "bottleneck_predictions.jsonl"), _records(root / "defect_predictions.jsonl")


def _risk(row, kind): return float(row.get(f"{kind}_risk_percent", row.get(f"{kind}_probability", 0) * 100) or 0)
def _confidence(row): return round(float(row.get("state_confidence", 0) or 0) * 100)
def _latest(rows, key):
    result = {}
    for row in rows: result[str(row.get(key, "—"))] = row
    return result
def _drivers(row):
    raw = (row.get("explanation") or {}).get("top_drivers") or row.get("top_risk_drivers") or []
    return ", ".join(str(x.get("label") or x.get("feature") or "driver") for x in raw[:3]) or "No explanation available"


def render_supervisor(context) -> None:
    st.header("Supervisor")
    run, bottlenecks, defects = _data(context)
    if not run:
        st.info("No completed run yet. Configure and run the factory above to populate the operational view."); return
    latest_b = _latest(bottlenecks, "station_id"); latest_d = _latest(defects, "unit_id")
    alerts = [r for r in latest_b.values() if r.get("warning")] + [r for r in latest_d.values() if r.get("warning")]
    cols = st.columns(5)
    cols[0].metric("Production day", run.production_day); cols[1].metric("Current run", run.run_id)
    cols[2].metric("Flow records", len(bottlenecks)); cols[3].metric("Active alerts", len(alerts))
    cols[4].metric("Observability", f"{sum(_confidence(x) for x in latest_b.values()) // max(len(latest_b), 1)}%")
    top = max(alerts, key=lambda r: max(_risk(r, "bottleneck"), _risk(r, "defect")), default=None)
    st.subheader("Most important current alert")
    if not top: st.success("No actionable current alerts in the latest predictions.")
    else:
        kind = "bottleneck" if "bottleneck_probability" in top else "defect"
        st.error(f"{kind.title()} risk {_risk(top, kind):.0f}% at {top.get('station_id', top.get('unit_id'))}")
        a, b, c = st.columns(3); a.write("**What happened?**\nRisk crossed its runtime alert threshold."); b.write(f"**Why?**\n{_drivers(top)}"); c.write(f"**What should the operator look at?**\nInspect {top.get('station_id', 'the flagged unit')} and its queue/evidence.")
    st.subheader("Top risky stations and units")
    left, right = st.columns(2)
    left.dataframe(sorted(({"Station": k, "Risk %": round(_risk(v, "bottleneck"),1), "Confidence %": _confidence(v)} for k,v in latest_b.items()), key=lambda x:x["Risk %"], reverse=True)[:3], hide_index=True, use_container_width=True)
    right.dataframe(sorted(({"Unit": k, "Risk %": round(_risk(v, "defect"),1), "Station": v.get("station_id")} for k,v in latest_d.items()), key=lambda x:x["Risk %"], reverse=True)[:3], hide_index=True, use_container_width=True)


def render_overview(context) -> None:
    """Prominent stakeholder mode landing view."""
    role = st.session_state.get("role", "Supervisor")
    if role == "Plant Manager":
        render_plant_manager(context)
    elif role == "Leadership":
        render_leadership(context)
    else:
        render_supervisor(context)


def render_plant_manager(context) -> None:
    st.header("Plant Manager")
    runs = context.run_history()
    if not runs: st.info("Historical trends will appear after completed production days are ingested."); return
    choice = st.selectbox("Scope", ["Current Run", "All Runs"] + [f"Production Day {r.production_day}" for r in runs])
    scoped = runs if choice == "All Runs" else ([next(r for r in runs if r.production_day == int(choice.split()[-1]))] if choice.startswith("Production") else [runs[0]])
    rows=[]; stations=Counter()
    for r in scoped:
        b=_records(Path(r.predictions_path or "") / "bottleneck_predictions.jsonl"); d=_records(Path(r.predictions_path or "") / "defect_predictions.jsonl")
        rows.append({"Day":r.production_day,"Bottleneck alerts":sum(bool(x.get("warning")) for x in b),"Defect alerts":sum(bool(x.get("warning")) for x in d),"Prediction flow":len(b)})
        stations.update(str(x.get("station_id")) for x in b if x.get("warning"))
    st.metric("Simulated production days represented", len(scoped)); st.line_chart(rows, x="Day", y=["Prediction flow", "Bottleneck alerts", "Defect alerts"])
    if stations:
        station, count=stations.most_common(1)[0]; st.info(f"Recurring Constraint\n\nStation {station}: high bottleneck risk on {count} of {len(scoped)} simulated days.")
    st.dataframe([{"Station":s,"Bottleneck-risk days":n} for s,n in stations.most_common()], hide_index=True, use_container_width=True)


def render_leadership(context) -> None:
    st.header("Leadership")
    runs=context.run_history(); stations=Station.all_from_factory(context.factory.data or {})
    a,b,c,d=st.columns(4); a.metric("Line count",1); b.metric("Stations",len(stations)); c.metric("Days simulated",len(runs)); d.metric("Factory coverage", f"{sum(s.sensor_coverage != 'NONE' for s in stations)/max(len(stations),1):.0%}")
    st.subheader("Operational impact")
    render_plant_manager(context)
    st.subheader("Scale story")
    st.markdown("**PILOT** — 1 LINE  \n↓  \n**PLANT** — MULTIPLE LINES  \n↓  \n**SITE** — MULTIPLE AREAS  \n↓  \n**MULTI-SITE**")
    st.caption("Reusable factory configuration · common BASE model · modular runtime · heterogeneous sensor coverage · dashboard analytics")
    st.subheader("Illustrative Prototype Business Impact")
    st.caption("Simulated/illustrative only. Derived from recorded prediction activity, not plant financials.")
    total=sum((r.metadata.get("bottleneck_stream") or {}).get("warning_count",0) for r in runs)
    st.metric("Actionable flow signals captured", total)


def render_live_twin(context) -> None:
    st.header("Live Digital Twin")
    _, b, _ = _data(context); latest=_latest(b,"station_id"); stations=Station.all_from_factory(context.factory.data or {})
    if not stations: st.info("No valid factory topology available."); return
    cards=[]
    for s in stations:
        key=f"S{s.id+1:02d}"; row=latest.get(key, {}); risk=_risk(row,"bottleneck")
        cards.append({"Station":key,"Type":s.archetype,"Context":s.zone,"Coverage":s.sensor_coverage,"Risk %":round(risk,1),"Status":"ALERT" if row.get("warning") else "Flowing"})
    st.caption("Actual factory topology; latest current-run station prediction is shown for each node.")
    st.dataframe(cards, hide_index=True, use_container_width=True)


def render_bottlenecks(context) -> None:
    """Station bottleneck risk, live during a run and afterwards from the same history.

    The timeline is read incrementally from the bottleneck stream the existing runtime
    is writing, so it fills in while the run executes; when the run ends the same
    accumulated history stays on screen for historical analysis.
    """
    from dashboard.views.live_bottlenecks import (
        render_bottleneck_timeline,
        resolve_feed,
        status_banner,
    )

    st.header("Bottleneck Intelligence")
    run = _run(context)
    predictions_path = run.predictions_path if run else None
    feed, session = resolve_feed(context, run.run_id if run else None, predictions_path)
    if feed is None:
        st.info("No run selected yet. Start a run above, or pick one from Run History.")
        return

    status_banner(session)
    st.subheader("Bottleneck probability over simulator time")
    render_bottleneck_timeline(feed, session)

    latest = feed.state.latest_by_station()
    if not latest:
        return
    st.subheader("Latest prediction per station")
    table = sorted(
        (
            {
                "Station": station,
                "Risk %": round(point.risk_percent, 1),
                "Alert": point.warning,
                "Confidence %": round((point.state_confidence or 0.0) * 100),
                "Zone": point.zone,
                "Predictions": len(feed.state.series(station) or ()),
            }
            for station, point in latest.items()
        ),
        key=lambda row: row["Risk %"],
        reverse=True,
    )
    st.bar_chart({row["Station"]: row["Risk %"] for row in table})
    st.dataframe(table, hide_index=True, use_container_width=True)


def render_defects(context) -> None:
    st.header("Defect Intelligence")
    _,_,d=_data(context)
    if not d: st.info("No defect stream for the selected run."); return
    latest=_latest(d,"unit_id"); table=sorted(({"Unit":k,"Risk %":round(_risk(v,"defect"),1),"Station":v.get("station_id"),"Inspection priority": "High" if v.get("warning") else "Monitor","Confidence %":_confidence(v),"Factors":_drivers(v)} for k,v in latest.items()),key=lambda x:x["Risk %"],reverse=True)
    st.bar_chart({x["Unit"]:x["Risk %"] for x in table[:20]}); st.dataframe(table[:50],hide_index=True,use_container_width=True)


def render_sensor_coverage(context) -> None:
    st.header("Sensor Coverage & Trust")
    _,b,_=_data(context); latest=_latest(b,"station_id"); stations=Station.all_from_factory(context.factory.data or {})
    if not stations: st.info("No valid factory configuration."); return
    table=[]
    for s in stations:
        row=latest.get(f"S{s.id+1:02d}",{}); table.append({"Station":f"S{s.id+1:02d}","Coverage":s.sensor_coverage,"Prediction confidence":f"{_confidence(row)}%" if row else "No prediction","Zone":s.zone})
    st.dataframe(table,hide_index=True,use_container_width=True)
    station=st.selectbox("Station", [x["Station"] for x in table]); item=next(x for x in table if x["Station"]==station)
    st.info(f"{station}\n\nCoverage: {item['Coverage']} · Confidence: {item['Prediction confidence']}\n\nSuggested improvement: candidate for low-cost sensing during the next maintenance window when coverage is limited.")


def render_what_if(context) -> None:
    st.header("What-If")
    _,b,_=_data(context); latest=_latest(b,"station_id")
    if not latest: st.info("Run the factory to enable illustrative sensitivity analysis."); return
    station=st.selectbox("Station",list(latest)); adjustment=st.slider("Cycle time adjustment",0,20,0)
    baseline=_risk(latest[station],"bottleneck"); adjusted=min(100,baseline*(1+adjustment/100))
    a,c=st.columns(2); a.metric("Baseline bottleneck risk",f"{baseline:.1f}%"); c.metric("Adjusted illustrative risk",f"{adjusted:.1f}%")
    st.caption("Illustrative sensitivity analysis based on the current risk signal; it does not run a second simulator. Downstream stations may see queue/flow impact if this constraint persists.")
