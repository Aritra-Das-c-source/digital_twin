from __future__ import annotations

import logging
from pathlib import Path
from datetime import datetime

from dashboard.domain.run import Run
from dashboard.storage.repositories import RunRepository
from dashboard.config import DashboardConfig
from dashboard.orchestration.existing_runtime_adapter import ExistingRuntimeAdapter

logger = logging.getLogger(__name__)

class RunManager:
    def __init__(self, adapter: ExistingRuntimeAdapter, repository: RunRepository, config: DashboardConfig):
        self.adapter = adapter
        self.repository = repository
        self.config = config

    def register_completed_run(self, run_dir: Path, factory_path: Path, scenario_name: str | None = None, multiplier: float = 60.0, is_demo: bool = False) -> Run:
        history = self.repository.get_all_runs()
        next_prod_day = 1
        if history:
            max_day = max((r.production_day for r in history if r.production_day is not None), default=0)
            next_prod_day = max_day + 1
            
        run = Run(
            id=run_dir.name,
            run_dir=run_dir,
            factory_path=factory_path,
            scenario_name=scenario_name or "unknown",
            multiplier=multiplier,
            is_demo=is_demo,
            production_day=next_prod_day,
            created_at=datetime.utcnow()
        )
        
        self.repository.add_run(run)
        return run

    def get_current_run(self) -> Run | None:
        runs = self.repository.get_all_runs()
        if not runs:
            return None
        return max(runs, key=lambda r: r.production_day if r.production_day is not None else 0)

    def get_run_history(self, limit: int = 100) -> list[Run]:
        runs = self.repository.get_all_runs()
        runs.sort(key=lambda r: r.production_day if r.production_day is not None else 0, reverse=True)
        return runs[:limit]

    def can_start_run(self) -> tuple[bool, str]:
        factory_path = self.adapter.discover_factory()
        if not factory_path:
            return False, "Factory configuration not found."
            
        return True, ""
