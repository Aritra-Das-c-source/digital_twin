from __future__ import annotations

def validate_factory(data: dict) -> list[str]:
    errors = []
    
    if "stations" not in data or not isinstance(data["stations"], list):
        errors.append("stations must be a list")
        return errors

    stations = data["stations"]
    if len(stations) < 3:
        errors.append("stations must have at least 3 entries")

    station_ids = []
    has_source = 0
    has_sink = 0

    for i, station in enumerate(stations):
        if not isinstance(station, dict):
            errors.append(f"Station {i} is not an object")
            continue
            
        sid = station.get("id")
        if not isinstance(sid, int):
            errors.append(f"Station {i} must have an int id")
        else:
            station_ids.append(sid)

        name = station.get("name")
        if not isinstance(name, str):
            errors.append(f"Station {i} must have a str name")

        arch = station.get("archetype")
        if arch not in ("AUTOMATED", "MANUAL", "INSPECTION"):
            errors.append(f"Station {i} must have a valid archetype")

        mct = station.get("meanCycleTimeMs")
        if not isinstance(mct, int) or mct <= 0:
            errors.append(f"Station {i} must have a positive int meanCycleTimeMs")

        cv = station.get("cycleTimeCV")
        if not (isinstance(cv, float) or isinstance(cv, int)) or cv <= 0 or cv >= 1.0:
            errors.append(f"Station {i} must have a positive float cycleTimeCV < 1.0")

        bc = station.get("bufferCapacity")
        if not isinstance(bc, int) or bc < 0:
            errors.append(f"Station {i} must have a non-negative int bufferCapacity")

        sc = station.get("sensorCoverage")
        if sc not in ("HIGH", "PARTIAL", "NONE"):
            errors.append(f"Station {i} must have a valid sensorCoverage")

        if station.get("source") is True:
            has_source += 1
        if station.get("sink") is True:
            has_sink += 1

    if len(set(station_ids)) != len(station_ids):
        errors.append("Station IDs must be unique")
    
    if station_ids and station_ids != list(range(len(station_ids))):
        errors.append("Station IDs must be sequential starting from 0")
        
    if has_source != 1:
        errors.append("Exactly one station must have source=true")
    if has_sink != 1:
        errors.append("Exactly one station must have sink=true")

    valid_ids = set(station_ids)

    dark_zones = data.get("darkZones")
    if dark_zones is not None:
        if not isinstance(dark_zones, list):
            errors.append("darkZones must be a list")
        else:
            for i, dz in enumerate(dark_zones):
                if not isinstance(dz, dict):
                    errors.append(f"Dark zone {i} is not an object")
                    continue
                if not isinstance(dz.get("id"), str):
                    errors.append(f"Dark zone {i} must have a str id")
                start_id = dz.get("startStationId")
                end_id = dz.get("endStationId")
                
                if not isinstance(start_id, int) or start_id not in valid_ids:
                    errors.append(f"Dark zone {i} must have a valid startStationId")
                if not isinstance(end_id, int) or end_id not in valid_ids:
                    errors.append(f"Dark zone {i} must have a valid endStationId")
                    
                if isinstance(start_id, int) and isinstance(end_id, int):
                    if start_id > end_id:
                        errors.append(f"Dark zone {i} startStationId must be <= endStationId")
                    if end_id - start_id + 1 > 4:
                        errors.append(f"Dark zone {i} corridor must be <= 4 stations")

    checkpoints = data.get("checkpoints")
    if checkpoints is not None:
        if not isinstance(checkpoints, list):
            errors.append("checkpoints must be a list")
        else:
            for i, cp in enumerate(checkpoints):
                if not isinstance(cp, dict):
                    errors.append(f"Checkpoint {i} is not an object")
                    continue
                sid = cp.get("stationId")
                if not isinstance(sid, int) or sid not in valid_ids:
                    errors.append(f"Checkpoint {i} must reference a valid stationId")

    return errors

def is_valid_factory(data: dict) -> bool:
    return len(validate_factory(data)) == 0
