from __future__ import annotations

from pathlib import Path
import logging
from datetime import datetime

from dashboard.domain.run import Run
from dashboard.storage.repositories import RunRepository
from dashboard.config import DashboardConfig
from dashboard.ingestion.bottleneck_reader import read_bottleneck_predictions
from dashboard.ingestion.defect_reader import read_defect_predictions
from dashboard.ingestion.runtime_reader import read_run_metadata, read_system_manifest, read_system_health

logger = logging.getLogger(__name__)

class RunIngestor:
    def __init__(self, repository: RunRepository, config: DashboardConfig):
        self.repository = repository
        self.config = config

    def ingest_completed_run(self, run_dir: Path, output_dir: Path | None = None, factory_path: Path | None = None, multiplier: float = 60.0, is_demo: bool = False) -> Run:
        history = self.repository.get_all_runs()
        next_prod_day = 1
        if history:
            max_day = max((r.production_day for r in history if r.production_day is not None), default=0)
            next_prod_day = max_day + 1
            
        run_meta = read_run_metadata(run_dir / "run_metadata.json") or {}
        scenario_name = run_meta.get("scenario_name", "unknown")
        
        system_health = None
        if output_dir:
            system_health = read_system_health(output_dir / "system_health.json")
            
        bottleneck_stats = read_bottleneck_predictions(run_dir / "bottleneck_predictions.jsonl")
        defect_stats = read_defect_predictions(run_dir / "defect_predictions.jsonl")
        
        metadata_json = {
            "run_metadata": run_meta,
            "system_health": system_health,
            "bottleneck_stats": bottleneck_stats,
            "defect_stats": defect_stats
        }
        
        run = Run(
            id=run_dir.name,
            run_dir=run_dir,
            factory_path=factory_path or Path("unknown"),
            scenario_name=scenario_name,
            multiplier=multiplier,
            is_demo=is_demo,
            production_day=next_prod_day,
            created_at=datetime.utcnow(),
            metadata_json=metadata_json
        )
        
        self.repository.add_run(run)
        return run

    def ingest_from_manifest(self, manifest_path: Path, factory_path: Path | None = None) -> Run | None:
        manifest = read_system_manifest(manifest_path)
        if not manifest:
            logger.warning(f"Could not read manifest at {manifest_path}")
            return None
            
        run_dir_str = manifest.get("run_dir")
        if not run_dir_str:
            logger.warning("Manifest missing run_dir")
            return None
            
        run_dir = Path(run_dir_str)
        output_dir = manifest_path.parent
        
        return self.ingest_completed_run(
            run_dir=run_dir,
            output_dir=output_dir,
            factory_path=factory_path,
            multiplier=manifest.get("multiplier", 60.0),
            is_demo=manifest.get("is_demo", False)
        )
