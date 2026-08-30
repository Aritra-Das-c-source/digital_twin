"""Run lifecycle coordination for the dashboard.

The RUN FACTORY action means "one complete execution of the existing pipeline", which
the dashboard records as one production day. This module owns that bookkeeping and
nothing else: it plans runs, reports readiness, and hands completed artifacts to the
ingestor. It never simulates, never predicts, and never writes upstream state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from dashboard.config import DashboardConfig
from dashboard.domain.run import Run, RunStatus
from dashboard.factory.manager import FactoryStatus, factory_state
from dashboard.orchestration.existing_runtime_adapter import (
    AdapterBoundary,
    ExistingRuntimeAdapter,
    PATHWAY_COORDINATED,
    RandomRunPlan,
)
from dashboard.storage.repositories import RunRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunReadiness:
    """Whether a factory run could be started, and what is blocking it."""

    ready: bool
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ready


class RunManager:
    """Plans production runs and reconciles them with the dashboard's history."""

    def __init__(
        self,
        config: DashboardConfig,
        adapter: ExistingRuntimeAdapter,
        repository: RunRepository | None = None,
    ):
        self.config = config
        self.adapter = adapter
        self.repository = repository

    # -- readiness --------------------------------------------------------------------

    def check_readiness(self) -> RunReadiness:
        """Report whether the existing pipeline could run, without starting anything."""
        blockers: list[str] = []
        warnings: list[str] = []

        if self.repository is None:
            # Without history the dashboard cannot record the production day a run
            # would become, so it does not offer to start one.
            blockers.append(
                "Dashboard database unavailable, so the run could not be recorded."
            )

        state = factory_state(self.config.factory_path)
        if state.status == FactoryStatus.MISSING:
            blockers.append(f"No factory configuration at {state.path}")
        elif state.status == FactoryStatus.INVALID:
            blockers.append(
                f"Factory configuration is invalid: {'; '.join(state.validation.errors[:3])}"
            )
        else:
            warnings.extend(state.validation.warnings)
            if state.is_demo:
                warnings.append(
                    "The configured factory is a generated demo definition, not a real "
                    "plant configuration."
                )

        if not self.adapter.simulator_available():
            blockers.append(
                "The C++ simulator is not built under simulation/build. Build it before "
                "starting a run."
            )

        return RunReadiness(
            ready=not blockers, blockers=tuple(blockers), warnings=tuple(warnings)
        )

    # -- planning ---------------------------------------------------------------------

    def next_production_day(self) -> int:
        return self.repository.next_production_day() if self.repository else 1

    def _next_free_run_id(self, start_day: int) -> tuple[str, int]:
        """First production-day id whose destination directories are all unoccupied.

        History may lag the filesystem -- a run can have been executed from the CLI and
        not yet ingested. Skipping occupied ids keeps the emitted command runnable
        instead of colliding with `cli.py`'s "directory already contains files" guard.
        """
        day = start_day
        for _ in range(1000):
            run_id = f"production_day_{day:04d}"
            generated = self.config.generated_root / run_id
            runs = self.config.runs_root / run_id
            occupied = (generated.exists() and any(generated.iterdir())) or (
                runs / "run_0001"
            ).exists()
            if not occupied:
                return run_id, day
            day += 1
        return f"production_day_{day:04d}", day

    def plan_next_run(
        self,
        *,
        pathway: str = PATHWAY_COORDINATED,
        duration_ms: int | None = None,
    ) -> RandomRunPlan:
        """Describe the next production day's run. Executes nothing.

        The plan is preflighted against the configured factory so the command it carries
        is one that will actually run.
        """
        run_id, day = self._next_free_run_id(self.next_production_day())
        state = factory_state(self.config.factory_path)
        return self.adapter.plan_random_run(
            factory_path=self.config.factory_path,
            generated_dir=self.config.generated_root / run_id,
            runs_dir=self.config.runs_root / run_id,
            output_dir=self.config.predictions_root / run_id,
            run_id=run_id,
            seed=self.config.default_seed + day,
            duration_ms=duration_ms or self.config.default_duration_ms,
            multiplier=self.config.default_multiplier,
            pathway=pathway,
            factory=state.data,
        )

    def start_run(self, plan: RandomRunPlan) -> None:
        """Hand execution to the existing system.

        Always raises :class:`AdapterBoundary` today: coordinated execution is owned by
        ``cli.py``. The exception carries the exact command to run. Kept as a method so
        a later iteration can implement it here without the UI changing.
        """
        raise AdapterBoundary(
            "Starting a factory run is delegated to the existing CLI pipeline.",
            plan.command,
        ) from None

    # -- history ----------------------------------------------------------------------

    def current_run(self) -> Run | None:
        """The most recent recorded run, or None when history is empty."""
        return self.repository.latest_run() if self.repository else None

    def run_history(self, limit: int = 200) -> list[Run]:
        return self.repository.list_runs(limit=limit) if self.repository else []

    def record_planned_run(self, plan: RandomRunPlan, *, is_demo: bool = False) -> Run:
        """Persist a PENDING row so a started run is visible before it completes."""
        if self.repository is None:
            raise RuntimeError("RunManager has no repository; cannot record a run")
        day = self.repository.next_production_day()
        run = Run(
            run_id=plan.run_id,
            production_day=day,
            status=RunStatus.PENDING,
            scenario_reference=str(plan.generated_dir),
            scenario_description=f"Random scenario, seed {plan.seed}",
            multiplier=plan.multiplier if plan.multiplier is not None else 0.0,
            seed=plan.seed,
            duration_ms=plan.duration_ms,
            factory_path=str(plan.factory_path),
            artifact_path=str(plan.expected_run_dir),
            predictions_path=str(plan.output_dir),
            is_demo=is_demo,
            metadata={"pathway": plan.pathway, "command": plan.command},
        )
        return self.repository.upsert_run(run)

    def discover_unrecorded_runs(self) -> list[Path]:
        """Completed run directories on disk that history does not know about yet."""
        if self.repository is None:
            return []
        known = {run.artifact_path for run in self.repository.list_runs(limit=1000)}
        return [
            run.path
            for run in self.adapter.list_completed_runs(self.config.runs_root)
            if str(run.path) not in known
        ]
