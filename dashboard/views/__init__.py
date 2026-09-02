"""Streamlit views.

Views read from a :class:`dashboard.context.DashboardContext` and never touch SQL,
the simulator, or the ML runtimes directly.
"""

from dashboard.views.operations import (
    render_bottlenecks,
    render_defects,
    render_leadership,
    render_live_twin,
    render_plant_manager,
    render_sensor_coverage,
    render_supervisor,
)
from dashboard.views.run_factory import render_run_factory
from dashboard.views.run_history import SELECTED_RUN_KEY, render_run_history

__all__ = [
    "SELECTED_RUN_KEY",
    "render_bottlenecks",
    "render_defects",
    "render_leadership",
    "render_live_twin",
    "render_plant_manager",
    "render_run_factory",
    "render_run_history",
    "render_sensor_coverage",
    "render_supervisor",
]
