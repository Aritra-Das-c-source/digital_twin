from __future__ import annotations
import random

def generate_demo_factory(seed: int = 42) -> dict:
    rnd = random.Random(seed)
    
    total_stations = rnd.randint(30, 50)
    
    # Pre-calculate counts based on total
    infeed_count = 1
    dispatch_count = 1
    # distribute the rest: body ~20%, paint ~20%, test ~15%, rest is GA
    remaining = total_stations - 2
    body_count = int(remaining * 0.25)
    paint_count = int(remaining * 0.25)
    test_count = int(remaining * 0.15)
    ga_count = remaining - body_count - paint_count - test_count
    
    stations = []
    
    # Track assigned types
    manual_count = 0
    inspection_count = 0
    target_manual = rnd.randint(4, 6)
    target_inspection = rnd.randint(3, 4)
    
    # We will pick indices for manual and inspection randomly from available middle stations
    available_indices = list(range(1, total_stations - 1))
    rnd.shuffle(available_indices)
    manual_indices = set(available_indices[:target_manual])
    inspection_indices = set(available_indices[target_manual:target_manual+target_inspection])
    
    for i in range(total_stations):
        is_source = (i == 0)
        is_sink = (i == total_stations - 1)
        
        name = f"Station_{i}"
        if i == 0:
            name = "Infeed"
        elif i < 1 + body_count:
            name = f"BodyShop_{i}"
        elif i < 1 + body_count + paint_count:
            name = f"Paint_{i}"
        elif i < total_stations - 1 - test_count:
            name = f"GeneralAssembly_{i}"
        elif i < total_stations - 1:
            name = f"FinalTest_{i}"
        else:
            name = "Dispatch"
            
        if i in manual_indices:
            arch = "MANUAL"
            mct = rnd.randint(55000, 65000)
            cv = rnd.uniform(0.15, 0.22)
            bc = 2
            sc = "NONE"
        elif i in inspection_indices:
            arch = "INSPECTION"
            mct = rnd.randint(48000, 56000)
            cv = rnd.uniform(0.10, 0.14)
            bc = rnd.randint(2, 3)
            sc = "PARTIAL"
        else:
            arch = "AUTOMATED"
            mct = rnd.randint(36000, 50000)
            cv = rnd.uniform(0.08, 0.12)
            if is_source:
                bc = 0
            elif is_sink:
                bc = rnd.randint(3, 5)
            else:
                bc = rnd.randint(3, 5)
            sc = "HIGH" if rnd.random() < 0.8 else "PARTIAL"
            
        station = {
            "id": i,
            "name": name,
            "archetype": arch,
            "meanCycleTimeMs": mct,
            "cycleTimeCV": round(cv, 4),
            "bufferCapacity": bc,
            "sensorCoverage": sc
        }
        if is_source:
            station["source"] = True
        if is_sink:
            station["sink"] = True
            
        stations.append(station)
        
    # Dark zones
    # One 3-station corridor in body shop
    body_start = 1
    body_dz_start = rnd.randint(body_start, body_start + body_count - 3)
    dz1 = {
        "id": "dz_body",
        "startStationId": body_dz_start,
        "endStationId": body_dz_start + 2
    }
    
    # One 2-station corridor in assembly
    ga_start = 1 + body_count + paint_count
    ga_dz_start = rnd.randint(ga_start, ga_start + ga_count - 2)
    dz2 = {
        "id": "dz_ga",
        "startStationId": ga_dz_start,
        "endStationId": ga_dz_start + 1
    }
    
    dark_zones = [dz1, dz2]
    
    # Checkpoints
    checkpoints = []
    checkpoint_stations = list(manual_indices.union(inspection_indices))
    rnd.shuffle(checkpoint_stations)
    for idx, sid in enumerate(checkpoint_stations[:rnd.randint(3, 4)]):
        checkpoints.append({
            "id": f"cp_{idx}",
            "stationId": sid,
            "type": rnd.choice(["RFID", "POWER_DRAW"])
        })
        
    return {
        "stations": stations,
        "darkZones": dark_zones,
        "checkpoints": checkpoints,
        "_generated": True,
        "_generator": "dashboard-demo",
        "_seed": seed
    }
