from __future__ import annotations

import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

def read_bottleneck_predictions(path: Path) -> dict:
    stats = {
        "prediction_count": 0,
        "warning_count": 0,
        "first_timestamp_ms": None,
        "last_timestamp_ms": None,
        "unique_stations": [],
        "routes": {}
    }
    
    if not path.exists():
        return stats
        
    unique_stations_set = set()
        
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    stats["prediction_count"] += 1
                    
                    if record.get("is_warning", False):
                        stats["warning_count"] += 1
                        
                    ts = record.get("timestamp_ms")
                    if ts is not None:
                        if stats["first_timestamp_ms"] is None or ts < stats["first_timestamp_ms"]:
                            stats["first_timestamp_ms"] = ts
                        if stats["last_timestamp_ms"] is None or ts > stats["last_timestamp_ms"]:
                            stats["last_timestamp_ms"] = ts
                            
                    station = record.get("station_id")
                    if station is not None:
                        unique_stations_set.add(str(station))
                        
                    route = record.get("route")
                    if route:
                        stats["routes"][route] = stats["routes"].get(route, 0) + 1
                        
                except json.JSONDecodeError:
                    continue
                    
        stats["unique_stations"] = list(unique_stations_set)
    except Exception as e:
        logger.warning(f"Error reading bottleneck predictions from {path}: {e}")
        
    return stats
