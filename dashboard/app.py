"""DigitalTwin.ai dashboard shell.

Launch with::

    py -m streamlit run dashboard/app.py

The dashboard sits downstream of the existing Digital Twin system. Rendering a page
reads artifacts and the dashboard's own SQLite file -- it never starts a simulation,
never runs a model, and never launches a factory run on load. The RUN FACTORY control
is explicit and hands execution to the existing CLI pipeline.

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

PAGES = {
    "Overview": render_overview,
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

        st.caption("Role")
        role = st.selectbox("Role", ROLES, index=0, label_visibility="collapsed")
        st.session_state["role"] = role

        st.divider()
        st.caption("Navigation")
        page = st.radio("Navigation", list(PAGES), index=0, label_visibility="collapsed")

        st.divider()
        if context.database_ready:
            st.caption(f"Database: ready (schema v{context.database.schema_version()})")
        else:
            st.caption("Database: unavailable")
            if context.database_error:
                st.caption(context.database_error)
    return page


def _render_run_factory_control(context: DashboardContext) -> None:
    """The RUN FACTORY action. Never fires on page load; never runs a simulation here."""
    readiness = context.readiness()
    left, right = st.columns([1, 3])
    with left:
        clicked = st.button(
            "▶  RUN FACTORY",
            type="primary",
            use_container_width=True,
            disabled=not readiness.ready,
        )
    with right:
        for blocker in readiness.blockers:
            st.warning(blocker)
        for warning in readiness.warnings:
            st.caption(f"⚠️ {warning}")

    if clicked:
        st.session_state["show_run_plan"] = True
    if not st.session_state.get("show_run_plan") or context.run_manager is None:
        return

    duration_label = st.selectbox(
        "Simulated production day",
        list(RUN_DURATIONS),
        index=0,
        help=(
            "Shorter days finish sooner. The coordinated replay runs a particle filter "
            "over every event, so wall-clock time scales with simulated duration."
        ),
    )
    duration_ms, wall_clock = RUN_DURATIONS[duration_label]
    plan = context.run_manager.plan_next_run(duration_ms=duration_ms)

    st.subheader(f"Run plan · {plan.run_id}")
    st.caption(
        "One run = one simulated production day. Dashboard-triggered execution is not "
        "wired up in this prototype step — run the command below, then use Run History "
        "→ Rebuild from artifacts to ingest the results."
    )
    st.caption(f"⏱️ Expect roughly **{wall_clock}** of wall-clock time. It is not hung.")

    for note in plan.notes:
        st.caption(f"ℹ️ {note}")

    if not plan.runnable:
        st.error("This run cannot start yet:")
        for blocker in plan.blockers:
            st.markdown(f"- {blocker}")
        st.caption("Resolve the above and press RUN FACTORY again for an updated command.")
        return

    shell = st.radio(
        "Shell",
        ["powershell", "cmd", "bash"],
        horizontal=True,
        index=0 if sys.platform.startswith("win") else 2,
    )
    st.code(plan.command_line(shell), language="bash")
    st.caption(
        "Verified before display: destination directories are free, the bottleneck model "
        "can score every station in this factory, and the defect consumer's dependencies "
        "are installed."
    )


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
