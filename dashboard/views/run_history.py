"""Run History view.

Shows the runs the dashboard has actually ingested. No history is invented: with an
empty database the view says so and offers a rebuild from completed run artifacts on
disk, which is the supported way to repopulate after the database is deleted.

Analytics are out of scope here. Selecting a run stores its id in session state so later
views can scope themselves to it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

if TYPE_CHECKING:
    from dashboard.context import DashboardContext
    from dashboard.domain.run import Run

SELECTED_RUN_KEY = "selected_run_id"

_STATUS_ICON = {
    "COMPLETED": "✅",
    "RUNNING": "🔄",
    "PENDING": "⏳",
    "PARTIAL": "⚠️",
    "FAILED": "❌",
}


def render_run_history(context: DashboardContext) -> None:
    st.header("Run History")
    st.caption("One completed run = one simulated production day.")

    if not context.database_ready or context.repository is None:
        st.warning(
            "The dashboard database is unavailable, so no run history can be shown. "
            "The rest of the system is unaffected."
        )
        if context.database_error:
            st.caption(context.database_error)
        return

    runs = context.run_history()
    if not runs:
        _render_empty_state(context)
        return

    st.caption(f"{len(runs)} production run(s) recorded")
    st.dataframe(
        [_row(run) for run in runs],
        use_container_width=True,
        hide_index=True,
    )

    _render_history_maintenance(context)

    st.subheader("Inspect a run")
    labels = {f"Day {run.production_day} — {run.run_id}": run.run_id for run in runs}
    choice = st.selectbox(
        "Select a production run",
        options=list(labels),
        index=None,
        placeholder="Choose a production run…",
    )
    if choice:
        run_id = labels[choice]
        st.session_state[SELECTED_RUN_KEY] = run_id
        selected = context.repository.get_run(run_id)
        if selected is not None:
            _render_detail(selected)


def _render_empty_state(context: DashboardContext) -> None:
    st.info("No completed production runs yet.")
    st.caption(
        "Runs appear here once the existing pipeline has produced completed run "
        "artifacts and the dashboard has ingested them. Use the Run Factory page to "
        "start the next production day."
    )
    _render_history_maintenance(context)


def _render_history_maintenance(context: DashboardContext) -> None:
    """Rebuild history from artifacts — the reason the database is safe to delete."""
    if context.ingestor is None:
        return
    with st.expander("Rebuild history from completed run artifacts"):
        st.caption(
            f"Scans `{context.config.runs_root}` for completed run directories and "
            "rebuilds the dashboard database from them. Reads only; the existing "
            "system's artifacts are never modified."
        )
        if st.button("Rebuild from artifacts"):
            try:
                result = context.ingestor.rebuild_from_artifacts()
            except Exception as error:
                st.error(f"Rebuild failed: {error}")
                return
            if result.count:
                st.success(f"Ingested {result.count} completed run(s).")
                st.rerun()
            else:
                st.info("No completed run directories were found to ingest.")
            if result.skipped:
                st.caption(f"Skipped {len(result.skipped)} incomplete director(ies).")

    with st.expander("Clear Dashboard History"):
        st.warning("This clears dashboard history only. Raw simulator/prediction artifacts are not deleted.")
        st.caption("Factory configuration, model files, runtime artifacts, simulator outputs and prediction JSONL remain untouched.")
        if st.button("Clear Dashboard History", type="secondary"):
            try:
                result = context.ingestor.clear_history()
            except Exception as error:
                st.error(f"Could not clear dashboard history: {error}")
            else:
                st.session_state.pop(SELECTED_RUN_KEY, None)
                st.success(f"Cleared {result.total} dashboard row(s). Raw artifacts were preserved.")
                st.rerun()

        if st.button("Clear + Rebuild from artifacts"):
            try:
                cleared, rebuilt = context.ingestor.clear_and_rebuild()
            except Exception as error:
                st.error(f"Clear + rebuild failed: {error}")
            else:
                st.success(f"Cleared {cleared.total} dashboard row(s) and rebuilt {rebuilt.count} run(s).")
                st.rerun()


def _row(run: Run) -> dict[str, object]:
    return {
        "Production Day": run.production_day,
        "Run ID": run.run_id,
        "Scenario": run.scenario_name or "—",
        "Multiplier": f"{run.multiplier:g}×" if run.multiplier else "—",
        "DARK particles": (run.metadata.get("particles") or run.metadata.get("system_run_manifest", {}).get("particles") or "3000"),
        "Model": "BASE",
        "Status": f"{_STATUS_ICON.get(run.status.value, '')} {run.status.value}".strip(),
        "Completed": _timestamp(run.completed_at),
        "Demo": "demo" if run.is_demo else "",
    }


def _timestamp(value: str | None) -> str:
    if not value:
        return "—"
    return value[:19].replace("T", " ")


def _render_detail(run: Run) -> None:
    if run.is_demo:
        st.warning("Prototype/demo run — figures are illustrative, not measured plant data.")

    left, right = st.columns(2)
    with left:
        st.metric("Production Day", run.production_day)
        st.metric("Status", run.status.value)
        st.metric("Seed", run.seed if run.seed is not None else "—")
    with right:
        st.metric("Scenario", run.scenario_name or "—")
        st.metric(
            "Simulated duration",
            f"{run.duration_ms / 3_600_000:.1f} h" if run.duration_ms else "—",
        )
        st.metric("Multiplier", f"{run.multiplier:g}×" if run.multiplier else "—")

    st.caption(f"Factory: `{run.factory_path}`  ·  fingerprint `{run.factory_fingerprint or '—'}`")
    if run.artifact_path:
        st.caption(f"Run artifacts: `{run.artifact_path}`")
    if run.predictions_path:
        st.caption(f"Prediction outputs: `{run.predictions_path}`")

    if run.metadata:
        with st.expander("Recorded run metadata"):
            st.json(run.metadata)
