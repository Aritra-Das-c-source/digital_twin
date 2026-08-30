"""DigitalTwin.ai Dashboard — Streamlit application shell.

Launch with:
    streamlit run dashboard/app.py

The dashboard is DOWNSTREAM of the existing Digital Twin system.
It reads completed run artifacts and never triggers simulation on page load.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from dashboard.config import DashboardConfig, load_config
from dashboard.factory.manager import ensure_factory, load_factory, validate_factory_file
from dashboard.storage.database import DashboardDatabase


def _init_config() -> DashboardConfig:
    """Load dashboard configuration (cached per session)."""
    if "config" not in st.session_state:
        st.session_state["config"] = load_config()
    return st.session_state["config"]


def _init_database(config: DashboardConfig) -> DashboardDatabase | None:
    """Initialize the dashboard database, returning None on failure."""
    if "db" not in st.session_state:
        try:
            db = DashboardDatabase(config.database_path)
            db.initialize()
            st.session_state["db"] = db
        except Exception as exc:
            st.session_state["db"] = None
            st.session_state["db_error"] = str(exc)
    return st.session_state.get("db")


def _factory_status(config: DashboardConfig) -> tuple[str, str, dict | None]:
    """Determine factory file status.

    Returns (status_label, status_emoji, factory_data_or_None).
    """
    factory_path = config.factory_json_path
    if not factory_path.is_file():
        return "MISSING", "🔴", None
    try:
        valid, errors = validate_factory_file(factory_path)
        if valid:
            data = load_factory(factory_path)
            return "VALID", "🟢", data
        else:
            return "INVALID", "🟡", None
    except Exception:
        return "INVALID", "🟡", None


def _render_sidebar(config: DashboardConfig) -> str:
    """Render the sidebar and return the selected navigation page."""
    with st.sidebar:
        st.title("DIGITALTWIN.AI")
        st.divider()

        # Factory status
        st.subheader("Factory")
        status_label, status_emoji, factory_data = _factory_status(config)
        st.text_input(
            "Factory Path",
            str(config.factory_json_path),
            disabled=True,
            label_visibility="collapsed",
        )
        st.markdown(f"**Status:** {status_emoji} {status_label}")

        if factory_data:
            stations = factory_data.get("stations", [])
            dark_zones = factory_data.get("darkZones", [])
            st.caption(
                f"{len(stations)} stations · {len(dark_zones)} dark zone(s)"
            )

        if status_label == "MISSING":
            if st.button("Generate Demo Factory", type="secondary"):
                try:
                    ensure_factory(config.factory_json_path)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to generate demo factory: {exc}")

        st.divider()

        # Current run info
        st.subheader("Current Run")
        db = st.session_state.get("db")
        current_run_display = "—"
        production_day_display = "—"

        if db and db.is_initialized():
            try:
                from dashboard.storage.repositories import RunRepository
                repo = RunRepository(db)
                runs = repo.list_runs(limit=1)
                if runs:
                    latest = runs[0]
                    current_run_display = latest.run_id
                    production_day_display = str(latest.production_day)
            except Exception:
                pass

        st.markdown(f"**Run:** {current_run_display}")
        st.markdown(f"**Production Day:** {production_day_display}")

        st.divider()

        # Role selector
        st.subheader("Role")
        role = st.selectbox(
            "View as",
            options=["Supervisor", "Plant Manager", "Leadership"],
            index=0,
            label_visibility="collapsed",
        )
        st.session_state["role"] = role

        st.divider()

        # Navigation
        st.subheader("Navigation")
        pages = [
            "🏠 Overview",
            "🏭 Live Twin",
            "🚧 Bottlenecks",
            "🔍 Defects",
            "📡 Sensor Coverage",
            "🔮 What-If",
            "📋 Run History",
        ]
        selected = st.radio(
            "Go to",
            options=pages,
            index=0,
            label_visibility="collapsed",
        )

        st.divider()

        # Database info
        db_status = "🟢 Connected" if (db and db.is_initialized()) else "🔴 Not initialized"
        st.caption(f"Database: {db_status}")
        if db and db.is_initialized():
            version = db.schema_version()
            st.caption(f"Schema version: {version}")

    return selected


def _render_overview(config: DashboardConfig) -> None:
    """Render the overview / home page."""
    st.header("🏠 Overview")

    col1, col2, col3 = st.columns(3)
    status_label, _, factory_data = _factory_status(config)

    with col1:
        st.metric("Factory Status", status_label)
    with col2:
        if factory_data:
            st.metric("Stations", len(factory_data.get("stations", [])))
        else:
            st.metric("Stations", "—")
    with col3:
        db = st.session_state.get("db")
        if db and db.is_initialized():
            from dashboard.storage.repositories import RunRepository
            repo = RunRepository(db)
            st.metric("Total Runs", repo.count_runs())
        else:
            st.metric("Total Runs", "—")

    st.info(
        "Welcome to the DigitalTwin.ai Dashboard. "
        "This is a prototype stakeholder interface. "
        "Use the sidebar to navigate between views."
    )

    if status_label == "MISSING":
        st.warning(
            "No factory configuration found. "
            "Use the **Generate Demo Factory** button in the sidebar, "
            "or place your factory.json at the configured path."
        )


def _render_placeholder(page_name: str) -> None:
    """Render a placeholder page for features not yet implemented."""
    st.header(page_name)
    st.info(
        f"The **{page_name}** view is a placeholder. "
        "It will be implemented in a future iteration."
    )


def main() -> None:
    """Main Streamlit application entry point."""
    st.set_page_config(
        page_title="DigitalTwin.ai",
        page_icon="🏭",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    config = _init_config()
    db = _init_database(config)

    selected_page = _render_sidebar(config)

    # Route to the selected page
    if selected_page == "📋 Run History":
        from dashboard.views.run_history import render_run_history
        render_run_history(db)
    elif selected_page == "🏠 Overview":
        _render_overview(config)
    elif selected_page == "🏭 Live Twin":
        _render_placeholder("Live Twin")
    elif selected_page == "🚧 Bottlenecks":
        _render_placeholder("Bottlenecks")
    elif selected_page == "🔍 Defects":
        _render_placeholder("Defects")
    elif selected_page == "📡 Sensor Coverage":
        _render_placeholder("Sensor Coverage")
    elif selected_page == "🔮 What-If":
        _render_placeholder("What-If")
    else:
        _render_placeholder(selected_page)


if __name__ == "__main__":
    main()
