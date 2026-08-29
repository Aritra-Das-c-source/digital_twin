"""End-to-end finalized V5 defect runtime pipeline with optional SHAP."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from runtime.defect_feature_runtime import DefectRuntimeFeatureBuilder
from ml.defect_model_runtime import DefectModelRuntime, DefectPrediction


class DigitalTwinDefectPipeline:
    """Persistent production pipeline. Instantiate once, then feed live records."""

    def __init__(
        self,
        *,
        stations_csv: str | Path,
        units_csv: str | Path,
        model_artifact_path: str | Path,
        config_path: str | Path,
        calibrator_path: str | Path,
        run_id: str = "LIVE",
        explain_mode: str = "warnings",
        shap_top_k: int = 3,
    ):
        explain_mode = str(explain_mode).strip().lower()
        if explain_mode not in {"off", "warnings", "all"}:
            raise ValueError("explain_mode must be: off, warnings, or all")
        if int(shap_top_k) < 1:
            raise ValueError("shap_top_k must be >= 1")

        self.explain_mode = explain_mode
        self.shap_top_k = int(shap_top_k)

        self.features = DefectRuntimeFeatureBuilder(
            stations_csv=stations_csv,
            units_csv=units_csv,
            run_id=run_id,
        )
        self.model = DefectModelRuntime(
            model_artifact_path=model_artifact_path,
            config_path=config_path,
            calibrator_path=calibrator_path,
        )

        if self.features.feature_names != self.model.features:
            raise RuntimeError(
                "Defect runtime feature builder and V5 CatBoost feature contracts differ"
            )

    def reset(self) -> None:
        self.features.reset()
        self.model.reset()

    def process_station_event(
        self,
        event: Mapping[str, Any],
    ) -> list[DefectPrediction]:
        packet = self.features.process_station_event(event)
        if packet is None:
            return []

        # For warnings mode, score first and only pay SHAP cost for actionable warnings.
        if self.explain_mode == "off":
            prediction = self.model.predict_packet(packet, explain=False)
            return [prediction]

        if self.explain_mode == "all":
            prediction = self.model.predict_packet(
                packet,
                explain=True,
                shap_top_k=self.shap_top_k,
            )
            return [prediction]

        prediction = self.model.predict_packet(packet, explain=False)
        if prediction.warning:
            exp = self.model.explain_feature_row(
                packet.features_30,
                top_k=self.shap_top_k,
                expected_probability=prediction.raw_defect_probability,
            )
            prediction.explanation_available = True
            prediction.explanation_method = exp["method"]
            prediction.shap_value_space = exp["shap_value_space"]
            prediction.shap_base_value_raw = exp["base_value_raw"]
            prediction.shap_reconstructed_probability = exp[
                "reconstructed_probability"
            ]
            prediction.shap_probability_reconstruction_error = exp[
                "probability_reconstruction_error"
            ]
            prediction.top_risk_drivers = exp["top_risk_drivers"]
            prediction.top_protective_drivers = exp["top_protective_drivers"]

        return [prediction]

    def process_sensor_reading(self, reading: Mapping[str, Any]) -> list[DefectPrediction]:
        self.features.process_sensor_reading(reading)
        return []

    def process_manual_check(self, check: Mapping[str, Any]) -> list[DefectPrediction]:
        self.features.process_manual_check(check)
        return []

    def process_record(self, record: Mapping[str, Any]) -> list[DefectPrediction]:
        r = dict(record)
        stream = str(r.pop("stream", "")).strip().lower()

        if stream == "station_event":
            return self.process_station_event(r)
        if stream == "sensor_reading":
            return self.process_sensor_reading(r)
        if stream == "manual_check":
            return self.process_manual_check(r)

        raise ValueError(
            "record.stream must be one of: station_event, sensor_reading, manual_check"
        )

    def summary(self) -> dict[str, Any]:
        return {
            "pipeline": "defect-v5-runtime-v3",
            "dark_zone_used": False,
            "prediction_trigger": "UNIT_ARRIVED",
            "target": "future final-inspection FAIL",
            "feature_builder": self.features.diagnostics(),
            "model": self.model.model_summary(),
            "feature_contract_match": self.features.feature_names == self.model.features,
            "explain_mode": self.explain_mode,
            "shap_top_k": self.shap_top_k,
        }
