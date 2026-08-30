from __future__ import annotations

import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

def read_defect_predictions(path: Path) -> dict:
    stats = {
        "prediction_count": 0,
        "warning_count": 0,
        "first_timestamp_ms": None,
        "last_timestamp_ms": None,
        "unique_stations": [],
        "unique_units": [],
        "mean_defect_probability": None
    }
    
    if not path.exists():
        return stats
        
    unique_stations_set = set()
    unique_units_set = set()
    total_prob = 0.0
    prob_count = 0
        
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    stats["prediction_count"] += 1
                    
                    if record.get("is_warning", False) or record.get("defect_probability", 0) > 0.5:
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
                        
                    unit = record.get("unit_id")
                    if unit is not None:
                        unique_units_set.add(str(unit))
                        
                    prob = record.get("defect_probability")
                    if prob is not None:
                        total_prob += float(prob)
                        prob_count += 1
                        
                except json.JSONDecodeError:
                    continue
                    
        stats["unique_stations"] = list(unique_stations_set)
        stats["unique_units"] = list(unique_units_set)
        if prob_count > 0:
            stats["mean_defect_probability"] = total_prob / prob_count
            
    except Exception as e:
        logger.warning(f"Error reading defect predictions from {path}: {e}")
        
    return stats
