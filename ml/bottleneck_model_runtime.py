"""Route-agnostic runtime inference for the frozen 28-feature bottleneck XGBoost model.

This module sits AFTER runtime_controller.py.  It does not know how a feature row
was produced (LIGHT, isolated DARK, or DARK corridor) and it never recomputes
features.  It only:

1. loads the frozen bottleneck_model_bundle.joblib once;
2. enforces the exact 28-feature training contract;
3. restores categorical dtypes exactly as used during training;
4. calls predict_proba();
5. applies the saved F2-selected decision threshold; and
6. returns a JSON-safe prediction while preserving routing/quality metadata.

The existing Dark Zone dark_zone_model_adapter.py is intentionally NOT used by
this production path.  Keep that file for Dark-only offline validation/debugging.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from datetime import date, datetime
import math
import sys

import joblib
import numpy as np
import pandas as pd

# Allow this module to be imported/executed directly during diagnostics.
if __package__ in (None, ""):
    project_root = str(Path(__file__).resolve().parents[1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from light_zone.light_zone_runtime import BOTTLENECK_FEATURES

try:
    from runtime.runtime_controller import FeaturePacket
except Exception:  # Allows this file to be inspected independently.
    FeaturePacket = Any  # type: ignore[misc,assignment]


_REQUIRED_BUNDLE_KEYS = {
    "model",
    "features",
    "categorical_features",
    "category_levels",
    "threshold",
}


def _json_safe(value: Any) -> Any:
    """Recursively convert numpy/pandas scalars and non-finite floats for APIs."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        x = float(value)
        return None if (math.isnan(x) or math.isinf(x)) else x
    if not isinstance(value, str):
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
    return value


@dataclass
class BottleneckPrediction:
    """One dashboard/API-ready result produced from one FeaturePacket."""

    run_id: str
    route: str
    trigger: str
    station_id: str
    prediction_time_ms: int
    bottleneck_probability: float
    bottleneck_risk_percent: float
    warning: bool
    threshold: float
    threshold_percent: float
    state_confidence: Optional[float]
    vehicle_id: Optional[str] = None
    event_id: Optional[str] = None
    event_sequence: Optional[int] = None
    unknown_categories: Optional[dict[str, list[str]]] = None
    dashboard_state: Optional[dict[str, Any]] = None

    def as_dict(self) -> dict[str, Any]:
        return _json_safe(
            {
                "run_id": self.run_id,
                "route": self.route,
                "trigger": self.trigger,
                "station_id": self.station_id,
                "prediction_time_ms": self.prediction_time_ms,
                "vehicle_id": self.vehicle_id,
                "event_id": self.event_id,
                "event_sequence": self.event_sequence,
                "bottleneck_probability": self.bottleneck_probability,
                "bottleneck_risk_percent": self.bottleneck_risk_percent,
                "warning": self.warning,
                "threshold": self.threshold,
                "threshold_percent": self.threshold_percent,
                "state_confidence": self.state_confidence,
                "unknown_categories": self.unknown_categories or {},
                "dashboard_state": self.dashboard_state,
            }
        )


class BottleneckModelRuntime:
    """Load the frozen model once and score Light/Dark 28-feature packets."""

    def __init__(self, model_bundle_path: str | Path):
        self.model_bundle_path = Path(model_bundle_path).expanduser().resolve()
        if not self.model_bundle_path.is_file():
            raise FileNotFoundError(f"Model bundle not found: {self.model_bundle_path}")

        bundle = joblib.load(self.model_bundle_path)
        missing = sorted(_REQUIRED_BUNDLE_KEYS - set(bundle))
        if missing:
            raise ValueError(f"Model bundle missing required keys: {missing}")

        self.model = bundle["model"]
        self.features = list(bundle["features"])
        self.categorical_features = list(bundle["categorical_features"])
        self.category_levels = {
            str(k): list(v) for k, v in bundle["category_levels"].items()
        }
        self.threshold = float(bundle["threshold"])
        self.threshold_objective = bundle.get("threshold_objective", "unknown")

        # This is the key integration gate: model, Light and Dark must all agree.
        if self.features != list(BOTTLENECK_FEATURES):
            raise ValueError(
                "Saved XGBoost feature contract does not match runtime 28-feature contract.\n"
                f"model={self.features}\nruntime={list(BOTTLENECK_FEATURES)}"
            )
        if len(self.features) != 28 or len(set(self.features)) != 28:
            raise ValueError("Frozen bottleneck model must contain 28 unique features")

        for col in self.categorical_features:
            if col not in self.features:
                raise ValueError(f"Categorical feature {col!r} is not in model features")
            if col not in self.category_levels:
                raise ValueError(f"No saved category levels for {col!r}")

    # ------------------------------------------------------------------
    # Strict model-input preparation
    # ------------------------------------------------------------------
    def prepare_features(
        self,
        rows: Mapping[str, Any] | Iterable[Mapping[str, Any]] | pd.DataFrame,
    ) -> pd.DataFrame:
        if isinstance(rows, pd.DataFrame):
            frame = rows.copy()
        elif isinstance(rows, Mapping):
            frame = pd.DataFrame([dict(rows)])
        else:
            frame = pd.DataFrame([dict(r) for r in rows])

        if frame.empty:
            raise ValueError("No rows supplied for XGBoost prediction")

        missing = [f for f in self.features if f not in frame.columns]
        if missing:
            raise ValueError(f"Prediction input missing frozen feature(s): {missing}")

        # Extras are intentionally ignored; XGBoost sees exactly the training X.
        X = frame[self.features].copy()

        for col in self.categorical_features:
            X[col] = pd.Categorical(
                X[col].astype("string"),
                categories=self.category_levels[col],
            )

        for col in self.features:
            if col not in self.categorical_features:
                X[col] = pd.to_numeric(X[col], errors="coerce").astype("float32")

        return X

    def inspect_features(self, row: Mapping[str, Any]) -> dict[str, Any]:
        supplied = dict(row)
        missing = [f for f in self.features if f not in supplied]
        unknown_categories: dict[str, list[str]] = {}

        for col in self.categorical_features:
            if col not in supplied or supplied[col] is None:
                continue
            value = str(supplied[col])
            known = set(map(str, self.category_levels[col]))
            if value not in known:
                unknown_categories[col] = [value]

        return {
            "schema_valid": not missing,
            "missing_features": missing,
            "unknown_categories": unknown_categories,
        }

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------
    def predict_feature_rows(
        self,
        rows: Mapping[str, Any] | Iterable[Mapping[str, Any]] | pd.DataFrame,
    ) -> pd.DataFrame:
        X = self.prepare_features(rows)
        probability = np.asarray(self.model.predict_proba(X)[:, 1], dtype=float)
        warning = probability >= self.threshold

        result = pd.DataFrame(
            {
                "bottleneck_probability": probability,
                "bottleneck_risk_percent": probability * 100.0,
                "warning": warning.astype(bool),
                "threshold": self.threshold,
                "threshold_percent": self.threshold * 100.0,
            }
        )
        result["state_confidence"] = pd.to_numeric(
            X["state_confidence"], errors="coerce"
        ).to_numpy(dtype=float)
        return result

    def predict_features(self, row: Mapping[str, Any]) -> dict[str, Any]:
        diagnostic = self.inspect_features(row)
        if not diagnostic["schema_valid"]:
            raise ValueError(
                f"Invalid model feature row; missing: {diagnostic['missing_features']}"
            )

        result = self.predict_feature_rows(row).iloc[0]
        return _json_safe(
            {
                "bottleneck_probability": float(result["bottleneck_probability"]),
                "bottleneck_risk_percent": float(result["bottleneck_risk_percent"]),
                "warning": bool(result["warning"]),
                "threshold": float(result["threshold"]),
                "threshold_percent": float(result["threshold_percent"]),
                "state_confidence": float(result["state_confidence"])
                if pd.notna(result["state_confidence"])
                else None,
                "unknown_categories": diagnostic["unknown_categories"],
            }
        )

    def predict_packet(self, packet: FeaturePacket) -> BottleneckPrediction:
        """Score a runtime_controller.FeaturePacket without route-specific logic."""
        result = self.predict_features(packet.features_28)
        return BottleneckPrediction(
            run_id=str(packet.run_id),
            route=str(packet.route),
            trigger=str(packet.trigger),
            station_id=str(packet.station_id),
            prediction_time_ms=int(packet.prediction_time_ms),
            vehicle_id=str(packet.vehicle_id) if packet.vehicle_id is not None else None,
            event_id=str(packet.event_id) if packet.event_id is not None else None,
            event_sequence=(
                int(packet.event_sequence) if packet.event_sequence is not None else None
            ),
            bottleneck_probability=float(result["bottleneck_probability"]),
            bottleneck_risk_percent=float(result["bottleneck_risk_percent"]),
            warning=bool(result["warning"]),
            threshold=float(result["threshold"]),
            threshold_percent=float(result["threshold_percent"]),
            state_confidence=result["state_confidence"],
            unknown_categories=dict(result["unknown_categories"]),
            dashboard_state=packet.dashboard_state,
        )

    def predict_packets(self, packets: Iterable[FeaturePacket]) -> list[BottleneckPrediction]:
        return [self.predict_packet(packet) for packet in packets]

    def model_summary(self) -> dict[str, Any]:
        return {
            "model_bundle": str(self.model_bundle_path),
            "feature_count": len(self.features),
            "features": list(self.features),
            "categorical_features": list(self.categorical_features),
            "threshold": self.threshold,
            "threshold_percent": self.threshold * 100.0,
            "threshold_objective": self.threshold_objective,
        }
