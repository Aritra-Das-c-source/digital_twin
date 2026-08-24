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

def build_pipeline(cat_features, num_features):
    transformers = []
    if num_features:
        transformers.append(('num', StandardScaler(), num_features))
    if cat_features:
        transformers.append(('cat', OneHotEncoder(handle_unknown='ignore'), cat_features))
        
    preprocessor = ColumnTransformer(transformers=transformers)
    return Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('classifier', LogisticRegression(class_weight='balanced', max_iter=2000, random_state=42))
    ])

def train_and_eval(name, train, val, test, cat_features, num_features):
    pipe = build_pipeline(cat_features, num_features)
    pipe.fit(train, train['target'])
    
    val_proba = pipe.predict_proba(val)[:, 1]
    thresh = find_best_threshold(val['target'], val_proba)
    
    test_proba = pipe.predict_proba(test)[:, 1]
    test_metrics = get_metrics(test['target'], test_proba, thresh)
    
    print(f"\n--- {name} (Test run_006) ---")
    print(f"PR-AUC: {test_metrics['pr_auc']:.4f}, ROC-AUC: {test_metrics['roc_auc']:.4f}")
    if name not in ['Time-Only', 'Wear-Only']:
        print(f"Precision: {test_metrics['precision']:.4f}, Recall: {test_metrics['recall']:.4f}, F2: {test_metrics['f2']:.4f}")
        print(f"TP: {test_metrics['tp']}, FP: {test_metrics['fp']}, FN: {test_metrics['fn']}, TN: {test_metrics['tn']}")
    return test_metrics

def main():
    df = pd.read_csv('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/data/defect_prediction_s15_multirun.csv')
    
    exclude_always = ['unit_id', 'prediction_time', 'run_id', 'target', 
                      'upstream_cycle_count', 'total_blocked_events', 
                      'upstream_station_count', 'sensor_station_count']
    
    wear_features = ['current_mean', 'temperature_mean', 'torque_mean', 'vibration_mean',
                     'current_max', 'temperature_max', 'torque_max', 'vibration_max']
                     
    base_features = [c for c in df.columns if c not in exclude_always]
    cat_all = ['vehicle_model', 'supplier_batch']
    
    num_a = [c for c in base_features if c not in cat_all]
    cat_a = list(cat_all)
    
    num_b = [c for c in num_a if c not in wear_features]
    cat_b = list(cat_all)
    
    num_c = list(num_a)
    cat_c = ['vehicle_model']
    
    num_d = list(num_b)
    cat_d = ['vehicle_model']
    
    num_time = ['prediction_time']
    cat_time = []
    
    num_wear = list(wear_features)
    cat_wear = []
    
    train = df[df['run_id'].isin(['run_001', 'run_002', 'run_003', 'run_004'])]
    val = df[df['run_id'] == 'run_005']
    test = df[df['run_id'] == 'run_006']
    
    print(f"TRAIN: {len(train)} rows, {train['target'].sum()} FAILs")
    
    results = {}
    
    results['Model A'] = train_and_eval('Model A', train, val, test, cat_a, num_a)
    results['Model B'] = train_and_eval('Model B', train, val, test, cat_b, num_b)
    results['Model C'] = train_and_eval('Model C', train, val, test, cat_c, num_c)
    results['Model D'] = train_and_eval('Model D', train, val, test, cat_d, num_d)
    results['Time-Only'] = train_and_eval('Time-Only', train, val, test, cat_time, num_time)
    results['Wear-Only'] = train_and_eval('Wear-Only', train, val, test, cat_wear, num_wear)
    
    pos_prev = float(test['target'].mean())
    results['No-Skill'] = {'pr_auc': pos_prev, 'roc_auc': 0.5}
    print(f"\n--- No-Skill ---")
    print(f"PR-AUC: {pos_prev:.4f}")
    
    os.makedirs('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/results', exist_ok=True)
    with open('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/results/defect_multirun_robustness.json', 'w') as f:
        json.dump(results, f, indent=4)
    print("\nSaved defect_multirun_robustness.json")

if __name__ == '__main__':
    main()
