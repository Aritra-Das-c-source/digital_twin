import os
import json
import joblib
import pandas as pd
import numpy as np

class DefectInferenceModel:
    def __init__(self, model_dir=None):
        """Initialize inference model by loading pipeline and metadata."""
        if model_dir is None:
            model_dir = os.path.join(os.path.dirname(__file__), 'models')
            
        self.model_path = os.path.join(model_dir, 'defect_lr_model_a.joblib')
        self.meta_path = os.path.join(model_dir, 'defect_lr_model_a_metadata.json')
        
        if not os.path.exists(self.model_path) or not os.path.exists(self.meta_path):
            raise FileNotFoundError(f"Model artifacts not found in {model_dir}")
            
        self.pipeline = joblib.load(self.model_path)
        with open(self.meta_path, 'r') as f:
            self.metadata = json.load(f)
            
        self.threshold = self.metadata['frozen_threshold']
        self.numeric_features = self.metadata['numeric_features']
        self.categorical_features = self.metadata['categorical_features']
        self.all_required = self.numeric_features + self.categorical_features
        
        self.preprocessor = self.pipeline.named_steps['preprocessor']
        self.classifier = self.pipeline.named_steps['classifier']
        self.scaler = self.preprocessor.named_transformers_['num']
        self.training_means = dict(zip(self.numeric_features, self.scaler.mean_))
        
        ohe = self.preprocessor.named_transformers_.get('cat')
        cat_names = list(ohe.get_feature_names_out(self.categorical_features)) if ohe else []
        self.all_transformed_names = self.numeric_features + cat_names
        
        self.binary_features = {
            's14_manual_fail': "Manual alignment issue at S14",
            's07_manual_fail': "Manual check failure at S07",
            'manual_fail_count': "Multiple upstream manual check failures"
        }

    def _get_directional_label(self, feature_name, raw_value, training_mean, contribution):
        if feature_name in self.binary_features:
            if raw_value > 0.5:
                return f"{self.binary_features[feature_name]} observed"
            else:
                return f"No {self.binary_features[feature_name].lower()} observed; baseline pattern increased model risk"
        
        direction_word = "Higher" if raw_value > training_mean else "Lower" if raw_value < training_mean else "Average"
        clean_name = feature_name.replace('_', ' ')
        return f"{direction_word} {clean_name} than training baseline"

    def predict_defect(self, feature_row):
        """
        Run defect inference on a single row of features.
        """
        if isinstance(feature_row, dict):
            df = pd.DataFrame([feature_row])
        elif isinstance(feature_row, pd.Series):
            df = pd.DataFrame([feature_row.to_dict()])
        elif isinstance(feature_row, pd.DataFrame):
            df = feature_row.copy()
            if len(df) > 1:
                raise ValueError("predict_defect expects a single row, but got multiple.")
        else:
            raise ValueError("feature_row must be dict, pandas Series, or DataFrame")
            
        unit_id = df.get('unit_id', pd.Series(['unknown'])).iloc[0]
            
        missing = [f for f in self.all_required if f not in df.columns]
        if missing:
            raise ValueError(f"Missing required features: {missing}")
            
        df = df[self.all_required]
        
        if df.isna().any().any():
            raise ValueError("Input contains missing values (NaN)")
            
        numeric_part = df[self.numeric_features].astype(float).values
        if np.isinf(numeric_part).any():
            raise ValueError("Input contains infinite numeric values")
            
        proba = float(self.pipeline.predict_proba(df)[0, 1])
        prediction = 1 if proba >= self.threshold else 0
        
        if proba < 0.30:
            risk_label = "LOW"
        elif proba < 0.50:
            risk_label = "MEDIUM"
        else:
            risk_label = "HIGH"
            
        transformed = self.preprocessor.transform(df)
        if hasattr(transformed, 'toarray'):
            transformed = transformed.toarray()
            
        coefs = self.classifier.coef_[0]
        contributions = transformed[0] * coefs
        
        contributors = []
        for i, val in enumerate(contributions):
            if val > 0:
                feat_name = self.all_transformed_names[i]
                
                if feat_name in self.numeric_features:
                    raw_val = float(df[feat_name].iloc[0])
                    t_mean = float(self.training_means[feat_name])
                    std_val = float(transformed[0, i])
                    label = self._get_directional_label(feat_name, raw_val, t_mean, val)
                else:
                    raw_val = 1.0
                    t_mean = None
                    std_val = float(transformed[0, i])
                    if "supplier_batch" in feat_name:
                        batch_name = feat_name.replace("supplier_batch_", "")
                        label = f"Supplier batch {batch_name} pattern associated with increased predicted risk"
                    elif "vehicle_model" in feat_name:
                        model_name = feat_name.replace("vehicle_model_", "")
                        label = f"Vehicle model {model_name} pattern associated with increased predicted risk"
                    else:
                        label = f"{feat_name} pattern associated with increased predicted risk"

                contributors.append({
                    "feature": feat_name,
                    "raw_value": raw_val,
                    "training_mean": t_mean,
                    "standardized_value": std_val,
                    "coefficient": float(coefs[i]),
                    "contribution": float(val),
                    "direction": "increases predicted risk",
                    "label": label
                })
                
        contributors.sort(key=lambda x: x['contribution'], reverse=True)
        top_5 = contributors[:5]
        
        return {
            "unit_id": str(unit_id),
            "prediction_point": "S15",
            "defect_risk_score": proba,
            "threshold": self.threshold,
            "prediction": prediction,
            "warning": prediction == 1,
            "risk_label": risk_label,
            "likely_contributing_factors": top_5,
            "disclaimer": "Feature contributions explain the Logistic Regression model score. They are associations that influenced predicted risk and are not confirmed physical root causes."
        }
