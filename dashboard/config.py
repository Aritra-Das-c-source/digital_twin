"""Configuration module for the DigitalTwin.ai dashboard."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DashboardConfig:
    """Configuration settings for the dashboard."""

    project_root: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[1]
    )
    factory_json_path: Path = field(default=None)  # type: ignore[assignment]
    database_path: Path = field(default=None)  # type: ignore[assignment]
    runs_root: Path = field(default=None)  # type: ignore[assignment]
    generated_root: Path = field(default=None)  # type: ignore[assignment]
    runtime_output_root: Path = field(default=None)  # type: ignore[assignment]
    default_seed: int = 42
    default_duration_ms: int = 28_800_000
    default_multiplier: float = 60.0

    def __post_init__(self) -> None:
        if self.factory_json_path is None:
            self.factory_json_path = self.project_root / "simulation" / "config" / "factory.json"
        if self.database_path is None:
            self.database_path = self.project_root / "dashboard" / "dashboard.db"
        if self.runs_root is None:
            self.runs_root = self.project_root / "simulation" / "training" / "runs"
        if self.generated_root is None:
            self.generated_root = self.project_root / "simulation" / "training" / "generated"
        if self.runtime_output_root is None:
            self.runtime_output_root = self.project_root / "runtime_output"


def load_config() -> DashboardConfig:
    """Load dashboard configuration, reading environment variables for overrides if present."""
    config = DashboardConfig()
    if "DT_FACTORY_JSON" in os.environ:
        config.factory_json_path = Path(os.environ["DT_FACTORY_JSON"])
    if "DT_DASHBOARD_DB" in os.environ:
        config.database_path = Path(os.environ["DT_DASHBOARD_DB"])
    if "DT_RUNS_ROOT" in os.environ:
        config.runs_root = Path(os.environ["DT_RUNS_ROOT"])
    if "DT_GENERATED_ROOT" in os.environ:
        config.generated_root = Path(os.environ["DT_GENERATED_ROOT"])
    if "DT_RUNTIME_OUTPUT" in os.environ:
        config.runtime_output_root = Path(os.environ["DT_RUNTIME_OUTPUT"])
    return config
