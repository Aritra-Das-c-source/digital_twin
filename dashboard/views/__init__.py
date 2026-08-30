"""Streamlit views.

Views read from a :class:`dashboard.context.DashboardContext` and never touch SQL,
the simulator, or the ML runtimes directly.
"""

from dashboard.views.operations import (
    render_bottlenecks,
    render_defects,
    render_live_twin,
    render_overview,
    render_sensor_coverage,
    render_what_if,
)
from dashboard.views.run_history import SELECTED_RUN_KEY, render_run_history

__all__ = [
    "SELECTED_RUN_KEY",
    "render_bottlenecks",
    "render_defects",
    "render_live_twin",
    "render_overview",
    "render_run_history",
    "render_sensor_coverage",
    "render_what_if",
]
