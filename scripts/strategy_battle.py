import json
import polars as pl
from datetime import date
from pathlib import Path
import subprocess
import yaml
import shutil

PROJECT_ROOT = Path("/Users/rohanahire/Documents/Personal_Projects/multibagger")
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

def run_diag(config_label, start, end):
    print(f"Running {config_label} from {start} to {end}...")
    cmd = [
        "uv", "run", "python", "scripts/portfolio_diagnostics.py",
        "--start", start,
        "--end", end
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running diagnostics for {config_label}: {result.stderr}")
        return None
    
    # Extract JSON from stdout (it prints the whole dict)
    try:
        raw_output = result.stdout
        json_start = raw_output.find("{")
        json_end = raw_output.rfind("}") + 1
        data = json.loads(raw_output[json_start:json_end])
        return data
    except Exception as e:
        print(f"Failed to parse JSON for {config_label}: {e}")
        return None

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f)

# 1. Load current (New Optimized) config
with open(CONFIG_PATH, "r") as f:
    new_opt_cfg = yaml.safe_load(f)

# 2. Define Previous Best config
prev_best_cfg = yaml.safe_load(yaml.dump(new_opt_cfg)) # deep copy
prev_best_cfg["backtest"]["exit_mode"] = "v3_trail_pct"
prev_best_cfg["backtest"]["pending_days"] = 8
prev_best_cfg["backtest"]["max_hold_days"] = 180
prev_best_cfg["backtest"]["signal_ranking_mode"] = "default"
prev_best_cfg["exit_strategy"]["trail_activation_r"] = 3.0
prev_best_cfg["exit_strategy"]["trail_pct"] = 0.15
prev_best_cfg["portfolio"]["max_positions"] = 15
prev_best_cfg["portfolio"]["risk_per_trade_pct"] = 0.025
prev_best_cfg["portfolio"]["regime_heat_mult_strong"] = 1.0 # No multiplier in old

periods = [
    ("2021-01-01", "2026-03-17", "In-Sample (Bull)"),
    ("2015-01-01", "2020-12-31", "Out-of-Sample (Mixed)")
]

results = []

for start, end, label in periods:
    # Run Prev Best
    save_config(prev_best_cfg)
    res_prev = run_diag(f"PrevBest_{label}", start, end)
    
    # Run New Optimized
    save_config(new_opt_cfg)
    res_new = run_diag(f"NewOpt_{label}", start, end)
    
    if res_prev and res_new:
        results.append({
            "period": label,
            "prev": {
                "total_return": res_prev["strategy"]["total_return_pct"] * 100,
                "cagr": res_prev["strategy"]["cagr"] * 100,
                "mdd": res_prev["strategy"]["max_drawdown_pct"] * 100,
                "calmar": res_prev["strategy"]["calmar"],
                "trades": res_prev["strategy"]["filled_trades"],
                "expectancy": res_prev["strategy"]["total_return_pct"] / (res_prev["strategy"]["filled_trades"] or 1) # Crude
            },
            "new": {
                "total_return": res_new["strategy"]["total_return_pct"] * 100,
                "cagr": res_new["strategy"]["cagr"] * 100,
                "mdd": res_new["strategy"]["max_drawdown_pct"] * 100,
                "calmar": res_new["strategy"]["calmar"],
                "trades": res_new["strategy"]["filled_trades"],
                "expectancy": res_new["strategy"]["total_return_pct"] / (res_new["strategy"]["filled_trades"] or 1)
            }
        })

print("\n" + "="*60)
print("STRATEGY BATTLE: PREVIOUS BEST vs NEW OPTIMIZED")
print("="*60)

for r in results:
    print(f"\nPERIOD: {r['period']}")
    print(f"{'Metric':<20} | {'Prev Best':<15} | {'New Optimized':<15} | {'Verdict'}")
    print("-" * 65)
    for m in ["total_return", "cagr", "mdd", "calmar", "trades"]:
        p_val = r["prev"][m]
        n_val = r["new"][m]
        better = "NEW" if (n_val > p_val if m != "mdd" else n_val > p_val) else "PREV"
        # MDD is negative, so higher is better
        
        p_str = f"{p_val:.2f}" + ("%" if m in ["total_return", "cagr", "mdd"] else "")
        n_str = f"{n_val:.2f}" + ("%" if m in ["total_return", "cagr", "mdd"] else "")
        
        print(f"{m:<20} | {p_str:<15} | {n_str:<15} | {better}")

# Restore the original config
save_config(new_opt_cfg)
