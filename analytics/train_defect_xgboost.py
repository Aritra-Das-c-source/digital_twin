import pandas as pd
import numpy as np
import os
import json
import xgboost as xgb
import shap
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.metrics import (
    average_precision_score, roc_auc_score, precision_score, 
    recall_score, f1_score, fbeta_score, confusion_matrix
)
from itertools import product
import warnings
warnings.filterwarnings("ignore", category=UserWarning)

def compute_bootstrap_ci(y_true, y_proba, threshold, n_iterations=2000):
    np.random.seed(42)
    y_true = np.array(y_true)
    y_proba = np.array(y_proba)
    n = len(y_true)
    
    metrics = {'pr_auc': [], 'roc_auc': [], 'precision': [], 'recall': [], 'f1': [], 'f2': []}
    
    for _ in range(n_iterations):
        indices = np.random.randint(0, n, n)
        y_true_boot = y_true[indices]
        y_proba_boot = y_proba[indices]
        
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

def find_best_threshold(y_true, y_proba):
    best_f2 = -1
    best_thresh = 0.5
    for t in np.arange(0.01, 1.0, 0.01):
        y_pred = (y_proba >= t).astype(int)
        f2 = fbeta_score(y_true, y_pred, beta=2, zero_division=0)
        if f2 > best_f2:
            best_f2 = f2
            best_thresh = t
    return best_thresh

def get_metrics(y_true, y_proba, threshold):
    y_pred = (y_proba >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()
    return {
        'threshold': float(threshold),
        'pr_auc': float(average_precision_score(y_true, y_proba)),
        'roc_auc': float(roc_auc_score(y_true, y_proba)),
        'precision': float(precision_score(y_true, y_pred, zero_division=0)),
        'recall': float(recall_score(y_true, y_pred, zero_division=0)),
        'f1': float(f1_score(y_true, y_pred, zero_division=0)),
        'f2': float(fbeta_score(y_true, y_pred, beta=2, zero_division=0)),
        'tn': int(tn), 'fp': int(fp), 'fn': int(fn), 'tp': int(tp)
    }

def build_pipeline(cat_features, num_features, params, scale_pos_weight):
    transformers = []
    if num_features:
        transformers.append(('num', 'passthrough', num_features))
    if cat_features:
        transformers.append(('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False), cat_features))
        
    preprocessor = ColumnTransformer(transformers=transformers)
    classifier = xgb.XGBClassifier(
        scale_pos_weight=scale_pos_weight,
        random_state=42,
        eval_metric='aucpr',
        use_label_encoder=False,
        **params
    )
    return Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('classifier', classifier)
    ])

def get_feature_names(pipeline, cat_features, num_features):
    ohe = pipeline.named_steps['preprocessor'].named_transformers_.get('cat')
    cat_names = list(ohe.get_feature_names_out(cat_features)) if ohe else []
    return list(num_features) + cat_names

def grid_search_loro(train_df, num_features, cat_features, scale_pos_weight):
    param_grid = {
        'max_depth': [2, 3, 4],
        'learning_rate': [0.03, 0.05, 0.1],
        'n_estimators': [100, 200, 300],
        'subsample': [0.8, 1.0],
        'colsample_bytree': [0.8, 1.0]
    }
    
    keys = param_grid.keys()
    combinations = [dict(zip(keys, v)) for v in product(*param_grid.values())]
    
    train_runs = ['run_001', 'run_002', 'run_003', 'run_004']
    best_params = None
    best_mean_pr_auc = -1
    best_mean_roc_auc = -1
    best_std_pr_auc = 0
    best_std_roc_auc = 0
    
    for params in combinations:
        pr_aucs = []
        roc_aucs = []
        for r in train_runs:
            t_tr = train_df[train_df['run_id'] != r]
            t_val = train_df[train_df['run_id'] == r]
            
            pipe = build_pipeline(cat_features, num_features, params, scale_pos_weight)
            pipe.fit(t_tr, t_tr['target'])
            
            y_prob = pipe.predict_proba(t_val)[:, 1]
            pr_aucs.append(average_precision_score(t_val['target'], y_prob))
            roc_aucs.append(roc_auc_score(t_val['target'], y_prob))
            
        mean_pr = np.mean(pr_aucs)
        if mean_pr > best_mean_pr_auc:
            best_mean_pr_auc = mean_pr
            best_mean_roc_auc = np.mean(roc_aucs)
            best_std_pr_auc = np.std(pr_aucs)
            best_std_roc_auc = np.std(roc_aucs)
            best_params = params
            
    return best_params, best_mean_pr_auc, best_std_pr_auc, best_mean_roc_auc, best_std_roc_auc

def evaluate_model(pipe, name, train, val, test, best_mean_pr, best_mean_roc):
    pipe.fit(train, train['target'])
    
    train_proba = pipe.predict_proba(train)[:, 1]
    train_pr = average_precision_score(train['target'], train_proba)
    train_roc = roc_auc_score(train['target'], train_proba)
    
    val_proba = pipe.predict_proba(val)[:, 1]
    thresh = find_best_threshold(val['target'], val_proba)
    val_metrics = get_metrics(val['target'], val_proba, thresh)
    
    test_proba = pipe.predict_proba(test)[:, 1]
    test_metrics = get_metrics(test['target'], test_proba, thresh)
    test_ci = compute_bootstrap_ci(test['target'], test_proba, thresh)
    
    overfitting = {
        'train_pr_auc': train_pr,
        'loro_pr_auc': best_mean_pr,
        'val_pr_auc': val_metrics['pr_auc'],
        'test_pr_auc': test_metrics['pr_auc']
    }
    
    return pipe, val_metrics, test_metrics, test_ci, overfitting, test_proba, thresh

def extract_shap(pipe, test, num_features, cat_features):
    # Preprocess the test data
    X_test_transformed = pipe.named_steps['preprocessor'].transform(test)
    feature_names = get_feature_names(pipe, cat_features, num_features)
    
    explainer = shap.TreeExplainer(pipe.named_steps['classifier'])
    shap_values = explainer.shap_values(X_test_transformed)
    
    mean_abs_shap = np.abs(shap_values).mean(axis=0)
    shap_df = pd.DataFrame({'feature': feature_names, 'mean_abs_shap': mean_abs_shap})
    shap_df = shap_df.sort_values('mean_abs_shap', ascending=False)
    
    return shap_values, X_test_transformed, feature_names, shap_df

def get_unit_explanations(pipe, test, test_proba, thresh, shap_values, feature_names):
    y_true = test['target'].values
    y_pred = (test_proba >= thresh).astype(int)
    unit_ids = test['unit_id'].values
    
    tp_idx = np.where((y_true == 1) & (y_pred == 1))[0]
    fp_idx = np.where((y_true == 0) & (y_pred == 1))[0]
    fn_idx = np.where((y_true == 1) & (y_pred == 0))[0]
    
    selected_idx = []
    if len(tp_idx) >= 2:
        selected_idx.extend(tp_idx[:2])
    elif len(tp_idx) == 1:
        selected_idx.append(tp_idx[0])
        
    if len(fp_idx) >= 1:
        selected_idx.append(fp_idx[0])
        
    if len(fn_idx) >= 1:
        selected_idx.append(fn_idx[0])
        
    explanations = []
    # Re-transform test data so we have the raw values for output
    X_trans = pipe.named_steps['preprocessor'].transform(test)
    
    for idx in selected_idx:
        sv = shap_values[idx]
        top_indices = np.argsort(np.abs(sv))[::-1][:5]
        
        top_contributors = []
        for ti in top_indices:
            top_contributors.append({
                'feature': feature_names[ti],
                'feature_value': float(X_trans[idx, ti]),
                'shap_value': float(sv[ti]),
                'direction': 'increases risk' if sv[ti] > 0 else 'decreases risk'
            })
            
        explanations.append({
            'unit_id': unit_ids[idx],
            'actual_target': int(y_true[idx]),
            'predicted_probability': float(test_proba[idx]),
            'prediction': int(y_pred[idx]),
            'top_5_contributors': top_contributors
        })
        
    return explanations

def main():
    df = pd.read_csv('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/data/defect_prediction_s15_multirun.csv')
    
    exclude_always = ['unit_id', 'prediction_time', 'run_id', 'target', 
                      'upstream_cycle_count', 'total_blocked_events', 
                      'upstream_station_count', 'sensor_station_count']
    
    wear_features = ['current_mean', 'temperature_mean', 'torque_mean', 'vibration_mean',
                     'current_max', 'temperature_max', 'torque_max', 'vibration_max']
                     
    base_features = [c for c in df.columns if c not in exclude_always]
    
    cat_a = ['vehicle_model', 'supplier_batch']
    num_a = [c for c in base_features if c not in cat_a]
    
    cat_d = ['vehicle_model']
    num_d = [c for c in base_features if c not in cat_d and c not in wear_features and c != 'supplier_batch']
    
    train = df[df['run_id'].isin(['run_001', 'run_002', 'run_003', 'run_004'])]
    val = df[df['run_id'] == 'run_005']
    test = df[df['run_id'] == 'run_006']
    
    num_neg = (train['target'] == 0).sum()
    num_pos = (train['target'] == 1).sum()
    scale_pos_weight = float(num_neg / num_pos)
    
    print(f"scale_pos_weight: {scale_pos_weight}")
    
    print("\n--- Grid Search XGB-A ---")
    best_params_a, mean_pr_a, std_pr_a, mean_roc_a, std_roc_a = grid_search_loro(train, num_a, cat_a, scale_pos_weight)
    print(f"Best Params XGB-A: {best_params_a}")
    
    print("\n--- Grid Search XGB-D ---")
    best_params_d, mean_pr_d, std_pr_d, mean_roc_d, std_roc_d = grid_search_loro(train, num_d, cat_d, scale_pos_weight)
    print(f"Best Params XGB-D: {best_params_d}")
    
    pipe_a = build_pipeline(cat_a, num_a, best_params_a, scale_pos_weight)
    pipe_a, val_metrics_a, test_metrics_a, test_ci_a, overfit_a, test_proba_a, thresh_a = evaluate_model(
        pipe_a, 'XGB-A', train, val, test, mean_pr_a, mean_roc_a
    )
    
    pipe_d = build_pipeline(cat_d, num_d, best_params_d, scale_pos_weight)
    pipe_d, val_metrics_d, test_metrics_d, test_ci_d, overfit_d, test_proba_d, thresh_d = evaluate_model(
        pipe_d, 'XGB-D', train, val, test, mean_pr_d, mean_roc_d
    )
    
    # SHAP
    shap_vals_a, X_trans_a, feats_a, shap_df_a = extract_shap(pipe_a, test, num_a, cat_a)
    shap_vals_d, X_trans_d, feats_d, shap_df_d = extract_shap(pipe_d, test, num_d, cat_d)
    
    unit_explanations_a = get_unit_explanations(pipe_a, test, test_proba_a, thresh_a, shap_vals_a, feats_a)
    
    # Save outputs
    results = {
        'split_counts': {
            'train': {'rows': len(train), 'fails': int(train['target'].sum())},
            'val': {'rows': len(val), 'fails': int(val['target'].sum())},
            'test': {'rows': len(test), 'fails': int(test['target'].sum())}
        },
        'scale_pos_weight': scale_pos_weight,
        'selected_params_a': best_params_a,
        'selected_params_d': best_params_d,
        'loro_results': {
            'xgb_a': {'mean_pr_auc': mean_pr_a, 'std_pr_auc': std_pr_a, 'mean_roc_auc': mean_roc_a, 'std_roc_auc': std_roc_a},
            'xgb_d': {'mean_pr_auc': mean_pr_d, 'std_pr_auc': std_pr_d, 'mean_roc_auc': mean_roc_d, 'std_roc_auc': std_roc_d}
        },
        'val_metrics_a': val_metrics_a,
        'val_metrics_d': val_metrics_d,
        'test_metrics_a': test_metrics_a,
        'test_metrics_d': test_metrics_d,
        'test_ci_a': test_ci_a,
        'test_ci_d': test_ci_d,
        'overfitting_a': overfit_a,
        'overfitting_d': overfit_d
    }
    
    os.makedirs('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/results', exist_ok=True)
    with open('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/results/defect_xgboost_results.json', 'w') as f:
        json.dump(results, f, indent=4)
        
    test_preds = test[['run_id', 'unit_id', 'prediction_time', 'target']].rename(columns={'target': 'actual_target'}).copy()
    test_preds['xgb_a_probability'] = test_proba_a
    test_preds['xgb_a_prediction'] = (test_proba_a >= thresh_a).astype(int)
    test_preds['xgb_d_probability'] = test_proba_d
    test_preds['xgb_d_prediction'] = (test_proba_d >= thresh_d).astype(int)
    test_preds.to_csv('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/results/defect_xgboost_test_predictions.csv', index=False)
    
    shap_df_combined = pd.DataFrame({
        'feature_xgb_a': shap_df_a['feature'].head(10).values,
        'mean_abs_shap_xgb_a': shap_df_a['mean_abs_shap'].head(10).values,
        'feature_xgb_d': shap_df_d['feature'].head(10).values if len(shap_df_d) >= 10 else list(shap_df_d['feature'].values) + [None]*(10-len(shap_df_d)),
        'mean_abs_shap_xgb_d': shap_df_d['mean_abs_shap'].head(10).values if len(shap_df_d) >= 10 else list(shap_df_d['mean_abs_shap'].values) + [None]*(10-len(shap_df_d))
    })
    shap_df_combined.to_csv('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/results/defect_xgboost_shap_global.csv', index=False)
    
    with open('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/results/defect_xgboost_shap_examples.json', 'w') as f:
        json.dump(unit_explanations_a, f, indent=4)
        
if __name__ == '__main__':
    main()
