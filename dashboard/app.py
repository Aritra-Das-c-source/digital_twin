"""DigitalTwin.ai dashboard shell.

Launch with::

    py -m streamlit run dashboard/app.py

The dashboard sits downstream of the existing Digital Twin system. Rendering a page
reads artifacts and the dashboard's own SQLite file -- it never starts a simulation,
never runs a model, and never launches a factory run on load. The RUN FACTORY control
is explicit and hands execution to the existing CLI pipeline, which runs as a
background process: the script never blocks on it, so the prediction streams that
run is writing can be read and charted while it is still executing.

Every prerequisite is optional at startup: a missing factory.json, a missing database,
an empty run history, absent prediction files and an idle runtime all render as empty
states.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # noqa: E402

from dashboard.context import DashboardContext, build_context  # noqa: E402
from dashboard.factory.manager import FactoryStatus  # noqa: E402
from dashboard.views import (  # noqa: E402
    render_bottlenecks,
    render_defects,
    render_live_twin,
    render_overview,
    render_run_history,
    render_sensor_coverage,
    render_what_if,
)

ROLES = ("Supervisor", "Plant Manager", "Leadership")

#: Selectable simulated-day lengths, mapped to (duration_ms, rough wall-clock estimate).
#: The coordinated replay runs a particle filter over every event, so wall-clock time
#: scales with simulated duration -- a full shift is a coffee break, not a crash.
RUN_DURATIONS: dict[str, tuple[int, str]] = {
    "Full shift — 8 simulated hours": (28_800_000, "20-30 minutes"),
    "Half shift — 4 simulated hours": (14_400_000, "10-15 minutes"),
    "Short — 1 simulated hour": (3_600_000, "3-5 minutes"),
    "Smoke test — 10 simulated minutes": (600_000, "under a minute"),
}

#: How often the run-status panel re-reads the live prediction stream while a run is
#: executing. Scoped to a Streamlit fragment, so the rest of the page stays usable.
LIVE_STATUS_REFRESH = "2s"

PAGES = {
    "Supervisor": render_overview,
    "Live Twin": render_live_twin,
    "Bottlenecks": render_bottlenecks,
    "Defects": render_defects,
    "Sensor Coverage": render_sensor_coverage,
    "What-If": render_what_if,
    "Run History": render_run_history,
}

_STATUS_ICON = {
    FactoryStatus.VALID: "🟢",
    FactoryStatus.INVALID: "🟠",
    FactoryStatus.MISSING: "🔴",
}


def get_context() -> DashboardContext:
    """Build the context once per session and reuse it across reruns."""
    if "context" not in st.session_state:
        st.session_state["context"] = build_context()
    return st.session_state["context"]


def _render_factory_block(context: DashboardContext) -> None:
    st.caption("Factory")
    st.code(str(context.config.factory_path), language=None)
    status = context.factory.status
    st.markdown(f"{_STATUS_ICON.get(status, '⚪')} **{status}**")

    if status == FactoryStatus.VALID:
        st.caption(
            f"{context.factory.station_count} stations · "
            f"{context.factory.dark_zone_count} DARK zone(s)"
        )
        if context.factory.is_demo:
            st.caption("⚠️ Generated demo configuration — illustrative, not plant data.")
        for warning in context.factory.validation.warnings:
            st.caption(f"⚠️ {warning}")
    elif status == FactoryStatus.INVALID:
        with st.expander("Why is this invalid?"):
            for error in context.factory.validation.errors:
                st.markdown(f"- {error}")
        st.caption("The file was left untouched. Fix it, or point the dashboard elsewhere.")
    else:
        st.caption("No configuration at this path.")
        if st.button("Generate demo factory", use_container_width=True):
            _generate_demo_factory(context)


def _generate_demo_factory(context: DashboardContext) -> None:
    from dashboard.factory.manager import generate_demo_factory

    try:
        generate_demo_factory(context.config.factory_path, seed=context.config.demo_seed)
    except FileExistsError:
        st.warning("A factory configuration already exists at that path; nothing was changed.")
    except Exception as error:
        st.error(f"Could not generate a demo factory: {error}")
    else:
        context.refresh_factory()
        st.rerun()


def _render_current_run_block(context: DashboardContext) -> None:
    st.caption("Current Run")
    latest = context.latest_run()
    if latest is None:
        st.markdown("**Run:** —")
        st.markdown("**Production Day:** —")
        st.caption("No completed production runs yet.")
        return
    st.markdown(f"**Run:** `{latest.run_id}`")
    st.markdown(f"**Production Day:** {latest.production_day}")
    st.caption(f"Status: {latest.status.value}" + ("  ·  demo" if latest.is_demo else ""))


def _render_sidebar(context: DashboardContext) -> str:
    with st.sidebar:
        st.title("DIGITALTWIN.AI")
        st.divider()
        _render_factory_block(context)
        st.divider()
        _render_current_run_block(context)
        st.divider()

        st.caption("Stakeholder mode")
        role = st.radio("Stakeholder mode", ROLES, index=0, label_visibility="collapsed")
        st.session_state["role"] = role

        st.divider()
        st.caption("Analysis")
        page = st.radio("Analysis", list(PAGES), index=0, label_visibility="collapsed")

        st.divider()
        if context.database_ready:
            st.caption(f"Database: ready (schema v{context.database.schema_version()})")
        else:
            st.caption("Database: unavailable")
            if context.database_error:
                st.caption(context.database_error)
    return page


def _live_registry():
    """The process-wide live-run registry.

    Imported lazily so the module-level import graph of the shell stays small and the
    registry is only touched by code paths that actually care about a run.
    """
    from dashboard.live.session import get_registry

    return get_registry()


def _current_session():
    """The run this process is executing, or the last one it executed."""
    registry = _live_registry()
    return registry.active_session() or registry.latest_session()


def _start_run(context: DashboardContext, plan) -> None:
    """Launch the planned run in the background and return immediately.

    The Streamlit script must never sit inside the pipeline: a run is a coffee break
    long, and blocking here would freeze every control on the page. The adapter starts
    the canonical ``cli.py`` command and a supervising thread drains its output and
    tails the prediction stream, so the bottleneck timeline fills in while the run is
    still executing.
    """
    from dashboard.live.session import bottleneck_stream_path

    session = _live_registry().create_session(
        plan.run_id, bottleneck_stream_path(plan.output_dir)
    )
    if context.run_manager is not None and context.repository is not None:
        # A PENDING row makes the run visible in history the moment it starts, and
        # carries the predictions path the timeline is read from.
        try:
            context.run_manager.record_planned_run(plan, is_demo=context.factory.is_demo)
        except Exception as error:  # a history hiccup must not block the run
            st.warning(f"The run started but could not be recorded yet: {error}")
    session.start(lambda: context.adapter.launch_planned_run(plan), plan=plan)
    st.session_state["selected_run_id"] = plan.run_id


def _ingest_finished_run(context: DashboardContext, session) -> None:
    """Record a finished run in history exactly once.

    This is bookkeeping, not a processing step the timeline waits on: the accumulated
    prediction history is already complete and displayable before this runs.
    """
    from dashboard.live.session import LiveRunStatus

    if session.ingested or context.ingestor is None or session.plan is None:
        return
    if session.status not in (LiveRunStatus.COMPLETED, LiveRunStatus.CANCELLED):
        return
    plan = session.plan
    try:
        run = context.ingestor.ingest_completed_run(
            plan.expected_run_dir,
            predictions_dir=plan.output_dir,
            run_id=plan.run_id,
            multiplier=plan.multiplier,
            particles=plan.particles,
            is_demo=context.factory.is_demo,
        )
    except Exception as error:
        # A run whose artifacts are incomplete (a cancelled one, typically) still keeps
        # every prediction it produced; only the history row is missing.
        st.caption(f"Run history was not updated: {error}")
        session.mark_ingested()
        return
    session.mark_ingested()
    st.session_state["selected_run_id"] = run.run_id


def _render_run_progress(context: DashboardContext, session) -> None:
    """Live status for the run this process launched."""
    from dashboard.live.session import LiveRunStatus

    progress = session.progress()
    columns = st.columns(5)
    columns[0].metric("Run", progress.run_id)
    columns[1].metric("Status", progress.status.value)
    columns[2].metric("Bottleneck predictions", progress.record_count)
    columns[3].metric("Stations seen", progress.station_count)
    columns[4].metric("Elapsed", f"{progress.elapsed_s / 60:.1f} min")

    if progress.status == LiveRunStatus.RUNNING:
        st.caption(
            "The pipeline is running as a background process. Predictions appear on the "
            "Bottlenecks page as the runtime emits them — there is no need to wait for "
            "the run to finish."
        )
        if st.button("Stop run", key="stop_live_run"):
            session.cancel()
            st.rerun()
    elif progress.status == LiveRunStatus.FAILED:
        st.error(progress.error or "The factory runtime failed.")
    elif progress.status == LiveRunStatus.CANCELLED:
        st.warning("The run was stopped. Predictions produced before it stopped are kept.")
    else:
        st.success("Run complete. Its prediction history stays available for analysis.")

    output = session.recent_output(limit=12)
    if output:
        with st.expander("Runtime output"):
            st.code("\n".join(output))


def _render_run_factory_control(context: DashboardContext) -> None:
    """The RUN FACTORY action. Never fires on page load, and never blocks the UI."""
    from dashboard.live.session import LiveRunStatus

    readiness = context.readiness()
    session = _current_session()
    running = session is not None and session.is_running

    with st.expander("Run Factory", expanded=True):
        cols = st.columns(4)
        cols[0].text_input("Factory", value=context.config.factory_path.name, disabled=True)
        cols[1].text_input("Scenario", value="Random", disabled=True)
        duration_label = cols[2].selectbox(
            "Duration", list(RUN_DURATIONS), index=3, disabled=running
        )
        from dashboard.orchestration.existing_runtime_adapter import (
            PLAYBACK_SPEED_MAX,
            PLAYBACK_SPEED_MIN,
        )

        multiplier = cols[3].slider(
            "Playback Speed",
            min_value=PLAYBACK_SPEED_MIN,
            max_value=PLAYBACK_SPEED_MAX,
            value=1.0,
            step=0.25,
            format="%.2fx",
            disabled=running,
            help=(
                "How fast simulated time advances relative to wall-clock time. 1x is "
                "approximately real-time; this paces the coordinated runtime's actual "
                "execution, not just what is displayed."
            ),
        )
        particles = st.slider("DARK particle count", 300, 3000, 3000, 100, disabled=running)
        st.caption("Model: BASE (fixed prototype model)")
        for blocker in readiness.blockers:
            st.warning(blocker)
        for warning in readiness.warnings:
            st.caption(f"Warning: {warning}")
        clicked = st.button(
            "RUN FACTORY", type="primary", disabled=running or not readiness.ready
        )

    if session is not None and session.status != LiveRunStatus.IDLE:
        _render_run_status_panel(context, session)

    if not clicked or context.run_manager is None:
        return

    duration_ms, _ = RUN_DURATIONS[duration_label]
    plan = context.run_manager.plan_next_run(
        duration_ms=duration_ms, multiplier=multiplier, particles=particles
    )
    if not plan.runnable:
        st.error("Run preflight failed:")
        for blocker in plan.blockers:
            st.markdown(f"- {blocker}")
        return
    # Shown only once preflight has verified it: the destination directories are free,
    # the pinned model can score every station, and the defect consumer's dependencies
    # are installed. This is the command the dashboard is about to run.
    st.code(
        plan.command_line("powershell" if sys.platform.startswith("win") else "bash"),
        language="bash",
    )
    try:
        _start_run(context, plan)
    except Exception as error:
        st.error(f"The factory run could not be started: {error}")
        return
    st.rerun()


def _render_run_status_panel(context: DashboardContext, session) -> None:
    """Run status, refreshing itself only while the pipeline is actually executing."""
    if not session.is_running:
        session.refresh()
        _ingest_finished_run(context, session)
        _render_run_progress(context, session)
        return

    @st.fragment(run_every=LIVE_STATUS_REFRESH)
    def _status_fragment() -> None:
        session.refresh()
        finished_now = not session.is_running
        _render_run_progress(context, session)
        if finished_now:
            _ingest_finished_run(context, session)
            # The whole page, not just this fragment, is stale once the run ends:
            # history, the sidebar's current run and every view need the new row.
            st.rerun(scope="app")

    _status_fragment()

def main() -> None:
    st.set_page_config(
        page_title="DigitalTwin.ai",
        page_icon="🏭",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    context = get_context()
    page = _render_sidebar(context)

    st.title("DIGITALTWIN.AI")
    st.caption(f"Prototype dashboard · viewing as {st.session_state.get('role', ROLES[0])}")

    for notice in context.notices:
        st.info(notice)

    _render_run_factory_control(context)
    st.divider()

    PAGES[page](context)


if __name__ == "__main__":
    main()
