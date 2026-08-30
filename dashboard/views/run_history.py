"""Run History view — displays persisted completed production runs."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dashboard.storage.database import DashboardDatabase


def render_run_history(db: DashboardDatabase | None) -> None:
    """Render the Run History page in Streamlit.

    Shows a table of completed production runs from the dashboard database,
    or an informational message when no runs exist yet.
    """
    import streamlit as st

    st.header("📋 Run History")

    if db is None or not db.is_initialized():
        st.info(
            "Dashboard database is not initialized. "
            "No historical run data is available."
        )
        return

    # Import repository lazily to avoid import-time side effects
    from dashboard.domain.run import Run, RunStatus
    from dashboard.storage.repositories import RunRepository

    repo = RunRepository(db)
    total = repo.count_runs()

    if total == 0:
        st.info("No completed production runs yet.")
        st.caption(
            "Production runs will appear here after the factory is executed "
            "and completed run artifacts are ingested into the dashboard."
        )
        return

    st.caption(f"{total} production run(s) recorded")

    runs = repo.list_runs(limit=200)

    # Build table data
    rows = []
    for run in runs:
        rows.append({
            "Production Day": run.production_day,
            "Run ID": run.run_id,
            "Scenario": run.scenario_name or "—",
            "Multiplier": f"{run.multiplier:.1f}×",
            "Status": _status_badge(run.status),
            "Started": _format_time(run.started_at),
            "Completed": _format_time(run.completed_at),
            "Demo": "🏷️ Demo" if run.is_demo else "",
        })

    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Production Day": st.column_config.NumberColumn(format="%d"),
            "Multiplier": st.column_config.TextColumn(),
        },
    )

    # Run selector for detailed inspection
    st.subheader("Inspect Run")
    run_ids = [r.run_id for r in runs]
    selected_id = st.selectbox(
        "Select a run to inspect",
        options=run_ids,
        index=None,
        placeholder="Choose a production run...",
    )

    if selected_id:
        selected_run = repo.get_run(selected_id)
        if selected_run:
            _render_run_detail(selected_run)
            # Store in session state so other views can reference it
            st.session_state["selected_run_id"] = selected_id


def _status_badge(status) -> str:
    """Return a human-readable status string with emoji."""
    from dashboard.domain.run import RunStatus

    mapping = {
        RunStatus.PENDING: "⏳ Pending",
        RunStatus.RUNNING: "🔄 Running",
        RunStatus.COMPLETED: "✅ Completed",
        RunStatus.FAILED: "❌ Failed",
        RunStatus.PARTIAL: "⚠️ Partial",
    }
    return mapping.get(status, str(status))


def _format_time(iso_str: str | None) -> str:
    """Format an ISO timestamp for display."""
    if not iso_str:
        return "—"
    try:
        # Show just date and time, no timezone
        return iso_str[:19].replace("T", " ")
    except (IndexError, TypeError):
        return iso_str


def _render_run_detail(run) -> None:
    """Render detailed information for a selected run."""
    import json

    import streamlit as st

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Production Day", run.production_day)
        st.metric("Status", _status_badge(run.status))
        st.metric("Multiplier", f"{run.multiplier:.1f}×")
    with col2:
        st.metric("Scenario", run.scenario_name or "—")
        st.metric("Demo Run", "Yes" if run.is_demo else "No")
        if run.artifact_path:
            st.text_input("Artifact Path", run.artifact_path, disabled=True)

    if run.metadata_json:
        try:
            metadata = json.loads(run.metadata_json)
            with st.expander("Run Metadata"):
                st.json(metadata)
        except (json.JSONDecodeError, TypeError):
            pass
