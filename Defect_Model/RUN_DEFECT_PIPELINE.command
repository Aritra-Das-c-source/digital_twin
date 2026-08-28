#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1
clear
echo "============================================================"
echo " FINAL V5 DEFECT PIPELINE"
echo "============================================================"
echo
python run_defect_pipeline.py
STATUS=$?
echo
if [ $STATUS -eq 0 ]; then
  echo "PIPELINE FINISHED SUCCESSFULLY"
  echo "Output:  results/latest_defect_predictions.jsonl"
  echo "Summary: results/latest_defect_run_summary.json"
else
  echo "PIPELINE FAILED - read the error above."
fi
echo
read -n 1 -s -r -p "Press any key to close..."
echo
exit $STATUS
