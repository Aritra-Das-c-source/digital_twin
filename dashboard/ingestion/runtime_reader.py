from __future__ import annotations

import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

def read_system_health(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read system health from {path}: {e}")
        return None

def read_system_manifest(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read system manifest from {path}: {e}")
        return None

def read_run_metadata(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read run metadata from {path}: {e}")
        return None
