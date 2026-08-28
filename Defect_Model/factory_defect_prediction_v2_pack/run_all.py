import argparse,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument("--simulation",required=True);p.add_argument("--force",action="store_true");a=p.parse_args();r=Path(__file__).resolve().parent
for s in ["train","validation","test"]:
 c=[sys.executable,str(r/"run_split.py"),"--split",s,"--simulation",a.simulation]+(["--force"] if a.force else [])
 if subprocess.run(c).returncode:sys.exit(1)
