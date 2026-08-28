import argparse,json,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument("--split",required=True,choices=["train","validation","test"]);p.add_argument("--simulation",required=True);p.add_argument("--force",action="store_true");a=p.parse_args();r=Path(__file__).resolve().parent;d=r/a.split;sim=Path(a.simulation).resolve()
if not sim.exists():sys.exit(f"Simulation executable not found: {sim}")
for i,x in enumerate(json.loads((d/"manifest.json").read_text()),1):
 c=d/x["config"];o=d/x["output"]
 if o.exists() and o.stat().st_size and not a.force:print(f"[{i}] SKIP {x['name']}");continue
 print(f"[{i}] RUN {x['name']} target={x['target_overall_defect_rate']*100:.1f}% {x['degradation_family']}")
 if subprocess.run([str(sim),"--config",str(c),"--output",str(o)]).returncode:sys.exit(f"FAILED {x['name']}")
