from __future__ import annotations

import json
import logging
from pathlib import Path
import sys

logger = logging.getLogger(__name__)

class ExistingRuntimeAdapter:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        
    def discover_factory(self) -> Path | None:
        default = self.default_factory_path()
        return default if default.exists() else None
        
    def default_factory_path(self) -> Path:
        return self.project_root / "simulation" / "config" / "factory.json"
        
    def is_completed_run(self, run_dir: Path) -> bool:
        required_files = ["stations.csv", "units.csv", "station_events.csv", "run_metadata.json"]
        return all((run_dir / f).exists() for f in required_files)
        
    def is_system_completed_run(self, run_dir: Path) -> bool:
        required_files = [
            "stations.csv", "units.csv", "station_events.csv", "run_metadata.json",
            "runtime_events.csv", "dz.csv", "station_checkpoints.csv"
        ]
        return all((run_dir / f).exists() for f in required_files)
        
    def list_completed_runs(self, runs_root: Path | None = None) -> list[dict]:
        if not runs_root:
            return []
        
        runs = []
        try:
            for d in runs_root.iterdir():
                if d.is_dir() and d.name.startswith("run_"):
                    is_completed = self.is_completed_run(d)
                    if is_completed:
                        runs.append({
                            "run_id": d.name,
                            "path": d,
                            "completed": True
                        })
        except Exception as e:
            logger.warning(f"Error listing completed runs in {runs_root}: {e}")
            
        return runs

    def read_run_metadata(self, run_dir: Path) -> dict | None:
        meta_path = run_dir / "run_metadata.json"
        if not meta_path.exists():
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read run metadata from {meta_path}: {e}")
            return None

    def read_system_health(self, output_dir: Path) -> dict | None:
        health_path = output_dir / "system_health.json"
        if not health_path.exists():
            return None
        try:
            with open(health_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read system health from {health_path}: {e}")
            return None

    def read_system_manifest(self, output_dir: Path) -> dict | None:
        manifest_path = output_dir / "system_run_manifest.json"
        if not manifest_path.exists():
            return None
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read system manifest from {manifest_path}: {e}")
            return None

    def prepare_random_run(self, *args, **kwargs) -> None:
        raise NotImplementedError("Programmatic run preparation requires future integration. Use 'python cli.py system run random' for now.")

    def start_run(self, *args, **kwargs) -> None:
        raise NotImplementedError("Programmatic run preparation requires future integration. Use 'python cli.py system run random' for now.")

    def get_scenario_generator(self):
        try:
            from simulation.training.scenario_generator import generate
            return generate
        except ImportError as e:
            logger.warning(f"Could not import scenario_generator: {e}")
            return None

    def get_run_orchestrator(self):
        try:
            from simulation.training.orchestrator import run_generated
            return run_generated
        except ImportError as e:
            logger.warning(f"Could not import orchestrator: {e}")
            return None
