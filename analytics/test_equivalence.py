import pandas as pd
import numpy as np
import os

new_df = pd.read_csv('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/data/run_001/defect_prediction_s15.csv')
orig_df = pd.read_csv('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/data/defect_prediction_s15.csv')

def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    return condition

# Pre-processing
if 'run_id' in new_df.columns:
    new_df = new_df.drop(columns=['run_id'])

if 'run_id' in orig_df.columns:
    orig_df = orig_df.drop(columns=['run_id'])

new_df = new_df.sort_values('unit_id').reset_index(drop=True)
orig_df = orig_df.sort_values('unit_id').reset_index(drop=True)

# 1. same number of rows
check("1. Same number of rows", len(new_df) == len(orig_df))

# 2. same set of unit_ids
check("2. Same set of unit_ids", (new_df['unit_id'] == orig_df['unit_id']).all())

# 3. same column names
new_df = new_df[orig_df.columns] # align columns
check("3. Same column names", list(new_df.columns) == list(orig_df.columns))

# 4. same target values
check("4. Same target values", (new_df['target'] == orig_df['target']).all())

# 5. same prediction_time values
check("5. Same prediction_time values", (new_df['prediction_time'] == orig_df['prediction_time']).all())

# 6. same categorical values
cat_cols = new_df.select_dtypes(include=['object']).columns
cat_match = True
for c in cat_cols:
    if not (new_df[c] == orig_df[c]).all():
        cat_match = False
check("6. Same categorical values", cat_match)

# 7. all numeric feature values identical within tolerance
num_cols = new_df.select_dtypes(include=[np.number]).columns
num_match = True
for c in num_cols:
    if not np.allclose(new_df[c].fillna(0), orig_df[c].fillna(0), rtol=1e-12, atol=1e-12):
        print(f"Mismatch in {c}")
        num_match = False
check("7. Numeric feature values identical within 1e-12", num_match)
