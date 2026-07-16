"""Reproduce the saved joint-WF candidate (~27% holdout) by replaying best_joint_params.json
through the production-parity harness (optimize_joint_wf.run_config)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import date
import optimize_joint_wf as J

p = json.loads(open("best_joint_params.json").read())
entry, exit_ = p["entry"], p["exit"]
print("entry:", entry)
print("exit :", exit_, "\n")

hold = J.run_config(entry, exit_, J.HOLDOUT_START, J.FULL_END)
full = J.run_config(entry, exit_, J.FULL_START, J.FULL_END)
cur_hold = J.run_config({}, {}, J.HOLDOUT_START, J.FULL_END)
cur_full = J.run_config({}, {}, J.FULL_START, J.FULL_END)

def show(tag, m):
    print(f"  {tag:24s} CAGR={m['cagr']*100:6.2f}%  MaxDD={m['mdd']*100:6.2f}%  "
          f"Calmar={m['calmar']:.2f}  trades={m['trades']}")

print("=== HOLDOUT 2023-01-01..2026-03-17 ===")
show("CANDIDATE (the ~27%)", hold)
show("CURRENT (committed)", cur_hold)
print("\n=== FULL HISTORY 2010..2026 ===")
show("CANDIDATE", full)
show("CURRENT (committed)", cur_full)
