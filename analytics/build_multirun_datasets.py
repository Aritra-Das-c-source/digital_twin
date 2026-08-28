import argparse
import os
import sys
import pandas as pd
import numpy as np
from scipy.stats import spearmanr

def check_path_safety(output_path):
    analytics_root = os.path.abspath('/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics')
    resolved_path = os.path.abspath(output_path)
    if not resolved_path.startswith(analytics_root):
        raise PermissionError(f"Safety Check Failed! Output path {resolved_path} is outside the analytics directory.")

def process_run(data_dir, out_dir, run_id):
    check_path_safety(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    
    print(f"\n{'='*50}\nPROCESSING {run_id}\n{'='*50}")
    
    # Load data
    events = pd.read_csv(os.path.join(data_dir, 'station_events.csv'))
    inspections = pd.read_csv(os.path.join(data_dir, 'inspection_results.csv'))
    units = pd.read_csv(os.path.join(data_dir, 'units.csv'))
    sensors = pd.read_csv(os.path.join(data_dir, 'sensor_readings.csv'))
    sensors = sensors.reset_index().rename(columns={'index': 'sensor_row_id'})
    manual = pd.read_csv(os.path.join(data_dir, 'manual_checks.csv'))

    # STEP 1 & 2: DEFINE COHORT & PREDICTION TIME
    s15_starts = events[(events['station_id'] == 'S15') & (events['event_type'] == 'PROCESSING_STARTED')].copy()
    s15_starts = s15_starts[['unit_id', 'timestamp_ms']].rename(columns={'timestamp_ms': 'prediction_time'})

    s40_inspections = inspections[inspections['station_id'] == 'S40']
    s40_counts = s40_inspections.groupby('unit_id').size()
    valid_s40_units = s40_counts[s40_counts == 1].index

    s40_results = s40_inspections[s40_inspections['unit_id'].isin(valid_s40_units)][['unit_id', 'result']]
    s40_results['target'] = (s40_results['result'] == 'FAIL').astype(int)

    cohort = pd.merge(s15_starts, s40_results[['unit_id', 'target']], on='unit_id', how='inner')
    cohort = pd.merge(cohort, units[['unit_id', 'vehicle_model', 'supplier_batch']], on='unit_id', how='inner')

    assert cohort['unit_id'].is_unique, "Duplicate unit_id in cohort"
    assert cohort['prediction_time'].notnull().all(), "Missing prediction_time"
    assert cohort['target'].isin([0, 1]).all(), "Target is not binary 0/1"

    # STEP 3 & 4: UPSTREAM EVENT FEATURES
    valid_stations = [f'S{i:02d}' for i in range(1, 15)]

    cohort_events = pd.merge(events, cohort[['unit_id', 'prediction_time']], on='unit_id', how='inner')
    cohort_events = cohort_events[cohort_events['timestamp_ms'] < cohort_events['prediction_time']]
    cohort_events = cohort_events[cohort_events['station_id'].isin(valid_stations)]

    assert (cohort_events['timestamp_ms'] < cohort_events['prediction_time']).all(), "Event timestamp leakage!"
    assert cohort_events['station_id'].isin(valid_stations).all(), "Event station leakage!"

    ct_events = cohort_events[cohort_events['event_type'] == 'PROCESSING_STARTED'].dropna(subset=['cycle_time_ms'])
    unit_station_counts = ct_events.groupby(['unit_id', 'station_id']).size()
    assert (unit_station_counts == 1).all(), "Multiple or missing PROCESSING_STARTED events for a unit at a single station!"

    ct_agg = ct_events.groupby('unit_id')['cycle_time_ms'].agg(
        total_cycle_time='sum',
        mean_cycle_time='mean',
        std_cycle_time='std',
        min_cycle_time='min',
        max_cycle_time='max',
        upstream_cycle_count='count'
    ).reset_index()

    bad_units = ct_agg[ct_agg['upstream_cycle_count'] != 14]
    if not bad_units.empty:
        raise AssertionError(f"Affected unit IDs lacking exactly 14 cycles: {bad_units['unit_id'].tolist()}")
    assert (ct_agg['upstream_cycle_count'] == 14).all()

    q_agg = cohort_events.groupby('unit_id').apply(lambda x: pd.Series({
        'mean_queue_length': x['queue_length_after'].mean(),
        'max_queue_length': x['queue_length_after'].max(),
        'total_blocked_events': (x['previous_state'] == 'BLOCKED').sum(),
        'total_starved_events': (x['previous_state'] == 'STARVED').sum(),
        'upstream_station_count': x['station_id'].nunique()
    }), include_groups=False).reset_index()

    event_features = pd.merge(ct_agg, q_agg, on='unit_id', how='outer')

    # STEP 5 & 3 (Window Integrity): SENSOR FEATURES
    raw_cohort_events = pd.merge(events, cohort[['unit_id', 'prediction_time']], on='unit_id', how='inner')
    raw_cohort_events = raw_cohort_events[raw_cohort_events['station_id'].isin(valid_stations)]

    starts = raw_cohort_events[raw_cohort_events['event_type'] == 'PROCESSING_STARTED'][['unit_id', 'station_id', 'timestamp_ms', 'prediction_time']]
    ends = raw_cohort_events[raw_cohort_events['event_type'] == 'PROCESSING_COMPLETED'][['unit_id', 'station_id', 'timestamp_ms']]
    windows = pd.merge(starts, ends, on=['unit_id', 'station_id'], suffixes=('_start', '_end'), how='inner')

    windows = windows[windows['timestamp_ms_start'] < windows['prediction_time']].copy()
    windows['effective_window_end'] = np.minimum(windows['timestamp_ms_end'], windows['prediction_time'])

    assert (windows['timestamp_ms_start'] < windows['effective_window_end']).all(), "Invalid window start/end"
    assert (windows['effective_window_end'] <= windows['prediction_time']).all(), "Window end crosses prediction time!"
    assert windows['station_id'].isin(valid_stations).all(), "Sensor window belongs to invalid station!"

    windows = windows.sort_values(['station_id', 'timestamp_ms_start'])
    windows['next_start'] = windows.groupby('station_id')['timestamp_ms_start'].shift(-1)
    overlaps = windows[windows['effective_window_end'] > windows['next_start']]
    if not overlaps.empty:
        raise ValueError(f"Sensor window overlap detected! {len(overlaps)} overlaps found.")

    sensor_aggs = []
    for idx, row in windows.iterrows():
        s_id = row['station_id']
        st = row['timestamp_ms_start']
        en = row['effective_window_end']
        u_id = row['unit_id']
        pred = row['prediction_time']
        
        mask = (sensors['station_id'] == s_id) & (sensors['timestamp_ms'] >= st) & (sensors['timestamp_ms'] < en)
        s_readings = sensors[mask].copy()
        
        if not s_readings.empty:
            s_readings['unit_id'] = u_id
            s_readings['prediction_time'] = pred
            sensor_aggs.append(s_readings)

    if sensor_aggs:
        matched_sensors = pd.concat(sensor_aggs)
        
        assert (matched_sensors['timestamp_ms'] < matched_sensors['prediction_time']).all(), "Leakage: sensor timestamp >= prediction_time!"
        assert matched_sensors['station_id'].isin(valid_stations).all(), "Invalid station in matched sensors!"
        assert matched_sensors['sensor_row_id'].is_unique, "A single sensor reading was assigned to multiple unit windows!"
        
        s_features = matched_sensors.groupby(['unit_id', 'sensor_type'])['value'].agg(['mean', 'std', 'max']).unstack('sensor_type')
        s_features.columns = [f"{col[1].lower()}_{col[0]}" for col in s_features.columns.values]
        s_features = s_features.reset_index()
        
        counts = matched_sensors.groupby('unit_id').agg(
            sensor_reading_count=('value', 'count'),
            sensor_station_count=('station_id', 'nunique')
        ).reset_index()
        
        sensor_features_df = pd.merge(s_features, counts, on='unit_id', how='outer')
    else:
        sensor_features_df = pd.DataFrame(columns=['unit_id'])

    # STEP 6: MANUAL CHECK FEATURES
    cohort_manual = pd.merge(manual, cohort[['unit_id', 'prediction_time']], on='unit_id', how='inner')
    cohort_manual = cohort_manual[cohort_manual['timestamp_ms'] < cohort_manual['prediction_time']]
    cohort_manual = cohort_manual[cohort_manual['station_id'].isin(valid_stations)]

    assert (cohort_manual['timestamp_ms'] < cohort_manual['prediction_time']).all(), "Manual check timestamp leakage!"
    assert cohort_manual['station_id'].isin(valid_stations).all(), "Manual check station leakage!"

    m_agg = cohort_manual.groupby('unit_id').apply(lambda x: pd.Series({
        'manual_check_count': len(x),
        'manual_fail_count': (x['result'] == 'FAIL').sum(),
        's07_manual_fail': 1 if ((x['station_id'] == 'S07') & (x['result'] == 'FAIL')).any() else 0,
        's14_manual_fail': 1 if ((x['station_id'] == 'S14') & (x['result'] == 'FAIL')).any() else 0
    }), include_groups=False).reset_index()

    # STEP 7 & 8: MERGE & AUTOMATED LEAKAGE ASSERTIONS
    final_df = cohort.copy()
    for df in [event_features, sensor_features_df, m_agg]:
        if not df.empty:
            final_df = pd.merge(final_df, df, on='unit_id', how='left')

    fill_zero_cols = ['manual_check_count', 'manual_fail_count', 's07_manual_fail', 's14_manual_fail', 
                      'total_blocked_events', 'total_starved_events', 'upstream_station_count', 
                      'sensor_reading_count', 'sensor_station_count']
    for col in fill_zero_cols:
        if col in final_df.columns:
            final_df[col] = final_df[col].fillna(0)

    cols = [c for c in final_df.columns if c != 'target'] + ['target']
    final_df = final_df[cols]
    final_df['run_id'] = run_id

    assert final_df['unit_id'].is_unique, "Duplicate unit_id exists"
    assert len(final_df) == len(cohort), "Exactly one row per unit failed"
    feature_cols = [c for c in final_df.columns if c not in ['unit_id', 'prediction_time', 'target', 'run_id']]
    assert not any('inspection' in c.lower() for c in feature_cols), "Inspection data found in features"
    assert 's15_cycle_time' not in final_df.columns, "S15 cycle time used!"
    
    out_path = os.path.join(out_dir, 'defect_prediction_s15.csv')
    check_path_safety(out_path)
    final_df.to_csv(out_path, index=False)
    print(f"Successfully processed {run_id} -> {out_path}")

def main():
    parser = argparse.ArgumentParser(description='Build datasets for explicitly trusted simulation runs.')
    parser.add_argument('--runs', nargs='+', required=True, help='Paths to trusted simulation runs')
    args = parser.parse_args()
    
    for run_path in args.runs:
        if not os.path.exists(run_path):
            print(f"Run path does not exist: {run_path}")
            sys.exit(1)
        run_id = os.path.basename(os.path.normpath(run_path))
        out_dir = f'/Users/kartik/Desktop/acc final/Prototype/digital_twin/analytics/data/{run_id}'
        process_run(run_path, out_dir, run_id)
        
    print("\nAdditional trusted simulator runs are required from the simulation owner before cross-run validation can proceed.")

if __name__ == '__main__':
    main()
