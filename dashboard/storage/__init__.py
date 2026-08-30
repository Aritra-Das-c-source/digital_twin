"""Dashboard-owned SQLite persistence.

Deleting the database costs the dashboard only its cached run history and derived
analytics; the simulator, the models and the coordinated runtime never read it, and
:mod:`dashboard.ingestion` rebuilds all of it from artifacts on disk.
"""

from dashboard.storage.analytics_repository import AnalyticsRepository
from dashboard.storage.database import DashboardDatabase
from dashboard.storage.migrations import LATEST_VERSION, apply_migrations, get_current_version
from dashboard.storage.repositories import RunRepository
from dashboard.storage.schema import SCHEMA_VERSION
from dashboard.storage.schema_v2 import ANALYTICS_TABLES, RUN_SCOPED_TABLES

__all__ = [
    "ANALYTICS_TABLES",
    "AnalyticsRepository",
    "DashboardDatabase",
    "LATEST_VERSION",
    "RUN_SCOPED_TABLES",
    "RunRepository",
    "SCHEMA_VERSION",
    "apply_migrations",
    "get_current_version",
]
