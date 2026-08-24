import pandas as pd
import numpy as np
import os
import json
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.linear_model import LogisticRegression
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
        'threshold': threshold,
        'pr_auc': average_precision_score(y_true, y_proba),
        'roc_auc': roc_auc_score(y_true, y_proba),
        'precision': precision_score(y_true, y_pred, zero_division=0),
        'recall': recall_score(y_true, y_pred, zero_division=0),
        'f1': f1_score(y_true, y_pred, zero_division=0),
        'f2': fbeta_score(y_true, y_pred, beta=2, zero_division=0),
        'tn': int(tn), 'fp': int(fp), 'fn': int(fn), 'tp': int(tp)
    }

def build_pipeline(cat_features, num_features):
    preprocessor = ColumnTransformer(
        transformers=[
            ('num', StandardScaler(), num_features),
            ('cat', OneHotEncoder(handle_unknown='ignore'), cat_features)
        ])
    return Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('classifier', LogisticRegression(class_weight='balanced', max_iter=2000, random_state=42))
    ])

def get_feature_names(pipeline, cat_features, num_features):
    ohe = pipeline.named_steps['preprocessor'].named_transformers_['cat']
    cat_names = ohe.get_feature_names_out(cat_features)
    return list(num_features) + list(cat_names)

def main():
    df = pd.read_csv('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/data/defect_prediction_s15_multirun.csv')
    
    # Exclusions
    exclude_always = ['unit_id', 'prediction_time', 'run_id', 'target', 
                      'upstream_cycle_count', 'total_blocked_events', 
                      'upstream_station_count', 'sensor_station_count']
    
    wear_features = ['current_mean', 'temperature_mean', 'torque_mean', 'vibration_mean',
                     'current_max', 'temperature_max', 'torque_max', 'vibration_max']
                     
    base_features = [c for c in df.columns if c not in exclude_always]
    cat_features = ['vehicle_model', 'supplier_batch']
    num_a = [c for c in base_features if c not in cat_features]
    num_b = [c for c in num_a if c not in wear_features]
    
    # Splits
    train = df[df['run_id'].isin(['run_001', 'run_002', 'run_003', 'run_004'])]
    val = df[df['run_id'] == 'run_005']
    test = df[df['run_id'] == 'run_006']
    
    split_counts = {
        'train': {'rows': len(train), 'fails': int(train['target'].sum())},
        'val': {'rows': len(val), 'fails': int(val['target'].sum())},
        'test': {'rows': len(test), 'fails': int(test['target'].sum())},
    }
    
    print(f"TRAIN: {len(train)} rows, {train['target'].sum()} FAILs")
    print(f"VAL: {len(val)} rows, {val['target'].sum()} FAILs")
    print(f"TEST: {len(test)} rows, {test['target'].sum()} FAILs")
    
    # MODEL A
    pipe_a = build_pipeline(cat_features, num_a)
    pipe_a.fit(train, train['target'])
    
    val_proba_a = pipe_a.predict_proba(val)[:, 1]
    thresh_a = find_best_threshold(val['target'], val_proba_a)
    val_metrics_a = get_metrics(val['target'], val_proba_a, thresh_a)
    
    test_proba_a = pipe_a.predict_proba(test)[:, 1]
    test_metrics_a = get_metrics(test['target'], test_proba_a, thresh_a)
    test_ci_a = compute_bootstrap_ci(test['target'], test_proba_a, thresh_a)
    
    feats_a = get_feature_names(pipe_a, cat_features, num_a)
    coefs_a = pipe_a.named_steps['classifier'].coef_[0]
    coef_df_a = pd.DataFrame({'feature': feats_a, 'coef': coefs_a}).sort_values('coef', ascending=False)
    
    # MODEL B
    pipe_b = build_pipeline(cat_features, num_b)
    pipe_b.fit(train, train['target'])
    
    val_proba_b = pipe_b.predict_proba(val)[:, 1]
    thresh_b = find_best_threshold(val['target'], val_proba_b)
    val_metrics_b = get_metrics(val['target'], val_proba_b, thresh_b)
    
    test_proba_b = pipe_b.predict_proba(test)[:, 1]
    test_metrics_b = get_metrics(test['target'], test_proba_b, thresh_b)
    test_ci_b = compute_bootstrap_ci(test['target'], test_proba_b, thresh_b)
    
    feats_b = get_feature_names(pipe_b, cat_features, num_b)
    coefs_b = pipe_b.named_steps['classifier'].coef_[0]
    coef_df_b = pd.DataFrame({'feature': feats_b, 'coef': coefs_b}).sort_values('coef', ascending=False)
    
    # LORO STABILITY ON TRAIN
    train_runs = ['run_001', 'run_002', 'run_003', 'run_004']
    loro_res_a = []
    loro_res_b = []
    for r in train_runs:
        t_tr = train[train['run_id'] != r]
        t_val = train[train['run_id'] == r]
        
        p_a = build_pipeline(cat_features, num_a)
        p_a.fit(t_tr, t_tr['target'])
        y_prob_a = p_a.predict_proba(t_val)[:, 1]
        loro_res_a.append({
            'run': r,
            'pr_auc': average_precision_score(t_val['target'], y_prob_a),
            'roc_auc': roc_auc_score(t_val['target'], y_prob_a)
        })
        
        p_b = build_pipeline(cat_features, num_b)
        p_b.fit(t_tr, t_tr['target'])
        y_prob_b = p_b.predict_proba(t_val)[:, 1]
        loro_res_b.append({
            'run': r,
            'pr_auc': average_precision_score(t_val['target'], y_prob_b),
            'roc_auc': roc_auc_score(t_val['target'], y_prob_b)
        })
        
    loro_a = pd.DataFrame(loro_res_a)
    loro_b = pd.DataFrame(loro_res_b)
    
    loro_summary = {
        'model_a': {
            'mean_pr_auc': float(loro_a['pr_auc'].mean()), 'std_pr_auc': float(loro_a['pr_auc'].std()),
            'mean_roc_auc': float(loro_a['roc_auc'].mean()), 'std_roc_auc': float(loro_a['roc_auc'].std())
        },
        'model_b': {
            'mean_pr_auc': float(loro_b['pr_auc'].mean()), 'std_pr_auc': float(loro_b['pr_auc'].std()),
            'mean_roc_auc': float(loro_b['roc_auc'].mean()), 'std_roc_auc': float(loro_b['roc_auc'].std())
        }
    }
    
    # NO SKILL BASELINE (run_006)
    pos_prev = test['target'].mean()
    always_pass_acc = 1 - pos_prev
    no_skill = {
        'positive_prevalence': float(pos_prev),
        'pr_auc': float(pos_prev),
        'always_pass_accuracy': float(always_pass_acc)
    }
    
    results = {
        'split_counts': split_counts,
        'val_metrics_a': val_metrics_a,
        'val_metrics_b': val_metrics_b,
        'test_metrics_a': test_metrics_a,
        'test_metrics_b': test_metrics_b,
        'test_ci_a': test_ci_a,
        'test_ci_b': test_ci_b,
        'loro_summary': loro_summary,
        'no_skill': no_skill,
        'top_coefs_a': {
            'pos': coef_df_a.head(5).to_dict('records'),
            'neg': coef_df_a.tail(5).to_dict('records')
        },
        'top_coefs_b': {
            'pos': coef_df_b.head(5).to_dict('records'),
            'neg': coef_df_b.tail(5).to_dict('records')
        }
    }
    
    os.makedirs('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/results', exist_ok=True)
    with open('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/results/defect_multirun_results.json', 'w') as f:
        json.dump(results, f, indent=4)
        
    test_preds = test[['run_id', 'unit_id', 'prediction_time', 'target']].rename(columns={'target': 'actual_target'}).copy()
    test_preds['model_a_probability'] = test_proba_a
    test_preds['model_a_prediction'] = (test_proba_a >= thresh_a).astype(int)
    test_preds['model_b_probability'] = test_proba_b
    test_preds['model_b_prediction'] = (test_proba_b >= thresh_b).astype(int)
    test_preds.to_csv('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/results/defect_multirun_test_predictions.csv', index=False)
    
if __name__ == '__main__':
    main()
