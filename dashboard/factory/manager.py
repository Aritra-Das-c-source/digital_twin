from __future__ import annotations
import json
from pathlib import Path

from dashboard.factory.validator import validate_factory, is_valid_factory
from dashboard.factory.generator import generate_demo_factory as _generate_demo_factory

def load_factory(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Factory file not found: {path}")
        
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    errors = validate_factory(data)
    if errors:
        raise ValueError(f"Invalid factory data: {', '.join(errors)}")
        
    return data

def validate_factory_file(path: Path) -> tuple[bool, list[str]]:
    if not path.exists():
        return False, [f"File not found: {path}"]
        
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return False, [f"Invalid JSON: {str(e)}"]
        
    errors = validate_factory(data)
    return len(errors) == 0, errors

def generate_demo_factory(path: Path, seed: int = 42) -> dict:
    if path.exists():
        raise FileExistsError(f"Factory file already exists: {path}")
        
    data = _generate_demo_factory(seed=seed)
    
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
        
    return data

def ensure_factory(path: Path, seed: int = 42) -> dict:
    if path.exists():
        return load_factory(path)
    return generate_demo_factory(path, seed=seed)
