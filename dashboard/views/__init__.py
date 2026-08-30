"""Streamlit views.

Views read from a :class:`dashboard.context.DashboardContext` and never touch SQL,
the simulator, or the ML runtimes directly.
"""

from dashboard.views.operations import (
    render_bottlenecks,
    render_configuration,
    render_defects,
    render_leadership,
    render_live_twin,
    render_overview,
    render_runtime_health,
    render_sensor_coverage,
)
from dashboard.views.run_history import SELECTED_RUN_KEY, render_run_history

__all__ = [
    "SELECTED_RUN_KEY",
    "render_bottlenecks",
    "render_configuration",
    "render_defects",
    "render_leadership",
    "render_live_twin",
    "render_overview",
    "render_runtime_health",
    "render_run_history",
    "render_sensor_coverage",
]
