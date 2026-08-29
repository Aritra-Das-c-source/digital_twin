from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory_models import (
    BASE_MODEL_ID,
    ARTIFACT_FILE,
    delete_model,
    list_models,
    model_paths,
    select_model,
    selected_model_id,
)


def _factory_artifact(root: Path, model_id: str) -> Path:
    directory = root / model_id
    (directory / "model").mkdir(parents=True)
    (directory / "calibration").mkdir()
    for relative in (
        "model/bottleneck_model_bundle.joblib",
        "model/bottleneck_xgboost.json",
        "configured_stations.csv",
        "calibration/historical_dwell.csv",
    ):
        (directory / relative).write_text("test", encoding="utf-8")
    (directory / ARTIFACT_FILE).write_text(
        json.dumps(
            {
                "model_id": model_id,
                "paths": {
                    "model_bundle": "model/bottleneck_model_bundle.joblib",
                    "xgboost_model": "model/bottleneck_xgboost.json",
                    "configured_stations": "configured_stations.csv",
                    "historical_dwell": "calibration/historical_dwell.csv",
                },
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_registry_switches_artifacts_without_mutating_them(tmp_path: Path) -> None:
    first = _factory_artifact(tmp_path, "factory-a")
    second = _factory_artifact(tmp_path, "factory-b")
    before_first = (first / ARTIFACT_FILE).read_bytes()
    before_second = (second / ARTIFACT_FILE).read_bytes()

    assert selected_model_id(tmp_path) == BASE_MODEL_ID
    select_model("factory-a", tmp_path)
    assert selected_model_id(tmp_path) == "factory-a"
    first_paths = model_paths(None, tmp_path)
    select_model("factory-b", tmp_path)
    second_paths = model_paths(None, tmp_path)

    assert first_paths["model_bundle"] != second_paths["model_bundle"]
    assert (first / ARTIFACT_FILE).read_bytes() == before_first
    assert (second / ARTIFACT_FILE).read_bytes() == before_second
    assert {item["id"] for item in list_models(tmp_path)} >= {BASE_MODEL_ID, "factory-a", "factory-b"}


def test_registry_protects_base_and_selected_artifact(tmp_path: Path) -> None:
    _factory_artifact(tmp_path, "factory-a")
    with pytest.raises(PermissionError):
        delete_model(BASE_MODEL_ID, tmp_path)

    select_model("factory-a", tmp_path)
    with pytest.raises(ValueError, match="selected"):
        delete_model("factory-a", tmp_path)

    select_model(BASE_MODEL_ID, tmp_path)
    delete_model("factory-a", tmp_path)
    assert not (tmp_path / "factory-a").exists()
