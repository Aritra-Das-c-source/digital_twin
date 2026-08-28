import pandas as pd
import numpy as np
import os
import json
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import (
    average_precision_score, roc_auc_score, precision_score, 
    recall_score, f1_score, fbeta_score, confusion_matrix
)
import warnings
warnings.filterwarnings("ignore", category=UserWarning)

def compute_bootstrap_ci(y_true, y_proba, threshold, n_iterations=2000, random_state=42):
    np.random.seed(random_state)
    n_size = len(y_true)
    metrics = { 'pr_auc': [], 'roc_auc': [], 'precision': [], 'recall': [], 'f1': [], 'f2': [] }
    
    y_true_np = np.array(y_true)
    y_proba_np = np.array(y_proba)
    
    for _ in range(n_iterations):
        idx = np.random.randint(0, n_size, size=n_size)
        y_true_boot = y_true_np[idx]
        y_proba_boot = y_proba_np[idx]
        
        if len(np.unique(y_true_boot)) < 2:
            continue
            
        y_pred_boot = (y_proba_boot >= threshold).astype(int)
        
        metrics['pr_auc'].append(average_precision_score(y_true_boot, y_proba_boot))
        metrics['roc_auc'].append(roc_auc_score(y_true_boot, y_proba_boot))
        metrics['precision'].append(precision_score(y_true_boot, y_pred_boot, zero_division=0))
        metrics['recall'].append(recall_score(y_true_boot, y_pred_boot, zero_division=0))
        metrics['f1'].append(f1_score(y_true_boot, y_pred_boot, zero_division=0))
        metrics['f2'].append(fbeta_score(y_true_boot, y_pred_boot, beta=2, zero_division=0))
        
    ci_results = {}
    for k, v in metrics.items():
        if v:
            ci_results[k] = {
                'lower_ci': float(np.percentile(v, 2.5)),
                'upper_ci': float(np.percentile(v, 97.5))
            }
        else:
            ci_results[k] = { 'lower_ci': 0.0, 'upper_ci': 0.0 }
    return ci_results

def get_alert_summary(cm):
    total_alerts = cm['FP'] + cm['TP']
    true_alerts = cm['TP']
    false_alerts = cm['FP']
    missed_defects = cm['FN']
    total_defects = cm['TP'] + cm['FN']
    alert_precision = true_alerts / total_alerts if total_alerts > 0 else 0
    defect_recall = true_alerts / total_defects if total_defects > 0 else 0
    return {
        'total_alerts': total_alerts,
        'true_alerts': true_alerts,
        'false_alerts': false_alerts,
        'missed_defects': missed_defects,
        'alert_precision': alert_precision,
        'defect_recall': defect_recall
    }

def main():
    data_dir = '/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/data'
    # Use run_001 dataset for baseline
    df = pd.read_csv(os.path.join(data_dir, 'run_001', 'defect_prediction_s15.csv')) if os.path.exists(os.path.join(data_dir, 'run_001', 'defect_prediction_s15.csv')) else pd.read_csv(os.path.join(data_dir, 'defect_prediction_s15.csv'))
    
    excluded_cols = ['unit_id', 'target', 'upstream_cycle_count', 
                     'total_blocked_events', 'upstream_station_count', 'sensor_station_count']
    if 'run_id' in df.columns:
        excluded_cols.append('run_id')
    
    target = df['target']
    df_features = df.drop(columns=[c for c in excluded_cols if c in df.columns])
    
    all_features = [c for c in df_features.columns if c != 'prediction_time']
    
    wear_features = [
        'current_mean', 'temperature_mean', 'torque_mean', 'vibration_mean',
        'current_max', 'temperature_max', 'torque_max', 'vibration_max'
    ]
    model_b_features = [c for c in all_features if c not in wear_features]
    
    categorical_features = ['vehicle_model', 'supplier_batch']
    
    def build_pipeline(features):
        num_features = [c for c in features if c not in categorical_features]
        preprocessor = ColumnTransformer(
            transformers=[
                ('num', StandardScaler(), num_features),
                ('cat', OneHotEncoder(handle_unknown='ignore'), categorical_features)
            ]
        )
        pipeline = Pipeline(steps=[
            ('preprocessor', preprocessor),
            ('classifier', LogisticRegression(class_weight='balanced', max_iter=2000, random_state=42))
        ])
        return pipeline

    def run_cv(features):
        seeds = [42, 123, 456, 789, 2026]
        pr_aucs = []
        roc_aucs = []
        X = df_features[features]
        y = target
        for seed in seeds:
            skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
            for train_idx, test_idx in skf.split(X, y):
                X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
                y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
                
                pipeline = build_pipeline(features)
                pipeline.fit(X_train, y_train)
                y_pred_proba = pipeline.predict_proba(X_test)[:, 1]
                pr_aucs.append(average_precision_score(y_test, y_pred_proba))
                roc_aucs.append(roc_auc_score(y_test, y_pred_proba))
                
        return {
            'pr_auc_mean': float(np.mean(pr_aucs)),
            'pr_auc_std': float(np.std(pr_aucs)),
            'roc_auc_mean': float(np.mean(roc_aucs)),
            'roc_auc_std': float(np.std(roc_aucs))
        }

    cv_results_a = run_cv(all_features)
    cv_results_b = run_cv(model_b_features)

    df_sorted = df.sort_values('prediction_time').reset_index(drop=True)
    split_idx = int(len(df_sorted) * 0.7)
    
    df_train = df_sorted.iloc[:split_idx]
    df_test = df_sorted.iloc[split_idx:]
    
    y_train = df_train['target']
    y_test = df_test['target']
    
    def evaluate_temporal(features):
        X_train = df_train[features]
        X_test = df_test[features]
        
        pipeline = build_pipeline(features)
        
        skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        y_train_pred_proba = cross_val_predict(pipeline, X_train, y_train, cv=skf, method='predict_proba')[:, 1]
        
        best_thresh = 0.5
        best_f2 = -1
        for thresh in np.arange(0.1, 0.9, 0.02):
            preds = (y_train_pred_proba >= thresh).astype(int)
            f2 = fbeta_score(y_train, preds, beta=2, zero_division=0)
            if f2 > best_f2:
                best_f2 = f2
                best_thresh = thresh
                
        pipeline.fit(X_train, y_train)
        y_test_proba = pipeline.predict_proba(X_test)[:, 1]
        y_test_pred = (y_test_proba >= best_thresh).astype(int)
        
        pr_auc = float(average_precision_score(y_test, y_test_proba))
        roc_auc = float(roc_auc_score(y_test, y_test_proba))
        precision = float(precision_score(y_test, y_test_pred, zero_division=0))
        recall = float(recall_score(y_test, y_test_pred, zero_division=0))
        f1 = float(f1_score(y_test, y_test_pred, zero_division=0))
        f2 = float(fbeta_score(y_test, y_test_pred, beta=2, zero_division=0))
        cm_array = confusion_matrix(y_test, y_test_pred, labels=[0, 1])
        cm = {'TN': int(cm_array[0,0]), 'FP': int(cm_array[0,1]), 'FN': int(cm_array[1,0]), 'TP': int(cm_array[1,1])}
        
        # Bootstrap CIs
        ci_results = compute_bootstrap_ci(y_test, y_test_proba, best_thresh, n_iterations=2000, random_state=42)
        alert_summary = get_alert_summary(cm)
        
        classifier = pipeline.named_steps['classifier']
        preprocessor = pipeline.named_steps['preprocessor']
        
        cat_feature_names = preprocessor.named_transformers_['cat'].get_feature_names_out(categorical_features)
        num_feature_names = [c for c in features if c not in categorical_features]
        all_feature_names = num_feature_names + list(cat_feature_names)
        
        coefs = classifier.coef_[0]
        feature_coefs = list(zip(all_feature_names, coefs))
        feature_coefs.sort(key=lambda x: x[1], reverse=True)
        
        top_pos = feature_coefs[:5]
        top_neg = feature_coefs[-5:]
        
        return {
            'threshold': best_thresh,
            'pr_auc': pr_auc,
            'roc_auc': roc_auc,
            'precision': precision,
            'recall': recall,
            'f1': f1,
            'f2': f2,
            'confusion_matrix': cm,
            'ci': ci_results,
            'alert_summary': alert_summary,
            'top_positive_coefs': [{'feature': f, 'coef': float(c)} for f, c in top_pos],
            'top_negative_coefs': [{'feature': f, 'coef': float(c)} for f, c in top_neg],
            'y_test_proba': y_test_proba,
            'y_test_pred': y_test_pred
        }

    temp_results_a = evaluate_temporal(all_features)
    temp_results_b = evaluate_temporal(model_b_features)
    
    # Save per-unit temporal predictions
    results_dir = os.path.join(os.path.dirname(data_dir), 'results')
    os.makedirs(results_dir, exist_ok=True)
    
    df_predictions = df_test[['unit_id', 'prediction_time', 'target']].copy()
    df_predictions.rename(columns={'target': 'actual_target'}, inplace=True)
    df_predictions['model_a_probability'] = temp_results_a['y_test_proba']
    df_predictions['model_a_prediction'] = temp_results_a['y_test_pred']
    df_predictions['model_b_probability'] = temp_results_b['y_test_proba']
    df_predictions['model_b_prediction'] = temp_results_b['y_test_pred']
    
    predictions_path = os.path.join(results_dir, 'defect_temporal_predictions.csv')
    df_predictions.to_csv(predictions_path, index=False)
    
    del temp_results_a['y_test_proba']
    del temp_results_a['y_test_pred']
    del temp_results_b['y_test_proba']
    del temp_results_b['y_test_pred']
    
    naive_pr_auc = y_test.mean()
    always_pass_acc = (y_test == 0).mean()
    
    results = {
        'dataset': {
            'total_units': len(df),
            'total_pass': int((df['target'] == 0).sum()),
            'total_fail': int((df['target'] == 1).sum()),
            'train_size': len(df_train),
            'test_size': len(df_test),
            'train_fails': int(y_train.sum()),
            'test_fails': int(y_test.sum())
        },
        'model_features': {
            'model_a_count': len(all_features),
            'model_b_count': len(model_b_features),
            'exclusions': excluded_cols + ['prediction_time']
        },
        'stratified_cv': {
            'model_a': cv_results_a,
            'model_b': cv_results_b
        },
        'temporal_holdout': {
            'model_a': temp_results_a,
            'model_b': temp_results_b
        },
        'naive_baselines': {
            'always_pass_accuracy': float(always_pass_acc),
            'no_skill_pr_auc': float(naive_pr_auc)
        }
    }
    
    results_path = os.path.join(results_dir, 'defect_baseline_results.json')
    with open(results_path, 'w') as f:
        json.dump(results, f, indent=2)
        
    print("==================================================")
    print("BASELINE DEFECT PREDICTION - STATISTICAL EVALUATION")
    print("==================================================")
    
    print("\n--- MODEL A CONFIDENCE INTERVALS ---")
    for metric in ['pr_auc', 'roc_auc', 'precision', 'recall', 'f1', 'f2']:
        pt = temp_results_a[metric]
        ci = temp_results_a['ci'][metric]
        print(f"{metric:>10}: {pt:.4f}  [95% CI: {ci['lower_ci']:.4f}, {ci['upper_ci']:.4f}]")
        
    print("\n--- MODEL B CONFIDENCE INTERVALS ---")
    for metric in ['pr_auc', 'roc_auc', 'precision', 'recall', 'f1', 'f2']:
        pt = temp_results_b[metric]
        ci = temp_results_b['ci'][metric]
        print(f"{metric:>10}: {pt:.4f}  [95% CI: {ci['lower_ci']:.4f}, {ci['upper_ci']:.4f}]")
        
    print("\n--- ALERT SUMMARIES ---")
    al_a = temp_results_a['alert_summary']
    al_b = temp_results_b['alert_summary']
    print(f"Model A: Generated {al_a['total_alerts']} alerts, of which {al_a['true_alerts']} were true defects (precision {al_a['alert_precision']:.2%}), catching {al_a['true_alerts']} of {al_a['true_alerts'] + al_a['missed_defects']} total defects (recall {al_a['defect_recall']:.2%}). False alerts: {al_a['false_alerts']}.")
    print(f"Model B: Generated {al_b['total_alerts']} alerts, of which {al_b['true_alerts']} were true defects (precision {al_b['alert_precision']:.2%}), catching {al_b['true_alerts']} of {al_b['true_alerts'] + al_b['missed_defects']} total defects (recall {al_b['defect_recall']:.2%}). False alerts: {al_b['false_alerts']}.")
    
    print(f"\nResults successfully updated in {results_path}")
    print(f"Per-unit temporal predictions saved to {predictions_path}")

if __name__ == '__main__':
    main()
