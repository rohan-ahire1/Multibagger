"""
Joint entry+exit walk-forward optimizer for SEPA (NSE), built on the PRODUCTION
engine (scripts/portfolio_diagnostics.py) so any winning params transfer 1:1 to
the committed backtest / live trade sheet.

Anti-overfit design:
  * Objective scored across 3 rolling in-sample regime folds (not one window).
  * Trial score = mean(Calmar) - LAMBDA * std(Calmar)  -> rewards consistency.
  * Hard holdout 2023-01-01..END is NEVER seen during optimization.
  * Final pick is a PLATEAU (robust neighborhood), not the single best trial.

Entry knobs (production-honored via filter_signals quality filters):
    rs_rank_min, vol_mult_min, voldry_min, max_signals_per_day
Exit knobs (production phased engine honored set):
    breakeven_activation_r, partial_profit_r, trail_pct_bull, trail_pct_bear,
    time_stop_days, trail_widen_r

Usage:
    python optimize_joint_wf.py --parity         # reproduce production baseline
    python optimize_joint_wf.py --trials 200     # run the study
    python optimize_joint_wf.py --report         # plateau pick + holdout report
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

_ROOT = Path(__file__).resolve().parent
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import logging
logging.getLogger("sepa_scanner").setLevel(logging.ERROR)

import scripts.portfolio_diagnostics as diag
from sepa_scanner.strategy.ranking import load_sector_map

FULL_START = date(2010, 1, 4)
FULL_END = date(2026, 3, 17)          # matches committed summary.json window
HOLDOUT_START = date(2023, 1, 1)      # never used in optimization objective

# In-sample regime folds (train/score windows). Each is a distinct market regime.
IS_FOLDS = [
    (date(2010, 1, 4), date(2014, 12, 31)),   # GFC recovery + 2013 taper tantrum
    (date(2014, 1, 1), date(2018, 12, 31)),   # mid-cycle bull + 2018 small-cap bust
    (date(2018, 1, 1), date(2022, 12, 31)),   # COVID crash + recovery + 2022 drawdown
]
LAMBDA = 0.5            # stability penalty weight
MIN_TRADES_PER_FOLD = 12
BUFFER_DAYS = 220       # let trades opened near fold end close out

# ---------------------------------------------------------------------------
# Load shared data ONCE
# ---------------------------------------------------------------------------
print("Loading production data bundle (full history)...")
_bundle, _signals, _ohlcv = diag.load_inputs(FULL_START, FULL_END)
_settings = _bundle["settings"]
_diagnostics = _bundle["diagnostics"]
_params = _bundle["params"]
_portfolio = _bundle["portfolio"]

_ref = Path("data/reference")
_full_map = _ref / "full_sector_map.csv"
_fallback = _ref / "nifty500_constituents.csv"
_sector_map = load_sector_map(_full_map if _full_map.exists() else _fallback)

_benchmark_curve = (
    _ohlcv.filter(pl.col("symbol") == _settings.market.benchmark_symbol)
    .select(["date", "close"]).rename({"close": "tri"})
    .filter((pl.col("date") >= FULL_START) & (pl.col("date") <= FULL_END))
    .sort("date")
)
# Regime entry controls are exogenous (benchmark-driven) -> compute once, slice per fold.
_regime_inputs = diag.compute_regime_inputs(_settings, FULL_START, FULL_END, _benchmark_curve)
_ENTRY_GATE: dict = {}
_ENTRY_SIZE_MULT: dict = {}
if _settings.backtest.use_regime_entry_controls:
    _ENTRY_GATE, _ENTRY_SIZE_MULT = diag.build_entry_controls(_settings, _regime_inputs)

_BASE_OVERRIDES = diag.resolve_signal_quality_overrides(_settings)
_BASE_EXIT = {
    "breakeven_activation_r": _settings.exit_strategy.breakeven_activation_r,
    "partial_profit_r": _settings.exit_strategy.partial_profit_r,
    "time_stop_days": _settings.exit_strategy.time_stop_days,
    "trail_pct_bull": _settings.exit_strategy.trail_pct_bull,
    "trail_pct_bear": _settings.exit_strategy.trail_pct_bear,
    "trail_widen_r": getattr(_settings.exit_strategy, "trail_widen_r", 3.0),
}
print(f"Data ready. Raw breakout signals: {_signals.height}\n")


# ---------------------------------------------------------------------------
def _metrics(daily_equity: pl.DataFrame, start: date, end: date):
    eq = daily_equity.filter((pl.col("date") >= start) & (pl.col("date") <= end)).sort("date")
    if eq.height < 20:
        return None
    e = eq["equity"].to_numpy()
    d0, d1 = eq["date"][0], eq["date"][-1]
    yrs = max((d1 - d0).days / 365.25, 1e-6)
    cagr = (e[-1] / e[0]) ** (1 / yrs) - 1
    peak = -1e18; mdd = 0.0
    for v in e:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    calmar = cagr / abs(mdd) if mdd < 0 else float("nan")
    return {"cagr": cagr, "mdd": abs(mdd), "calmar": calmar, "final": float(e[-1])}


def run_config(
    entry_ov: dict,
    exit_ov: dict,
    start: date,
    end: date,
    *,
    simulation_ov: dict | None = None,
    portfolio_ov: dict | None = None,
    params_ov: dict | None = None,
):
    """Run the production engine for a window with entry/exit overrides applied."""
    win_lo = start
    win_hi = end + timedelta(days=BUFFER_DAYS)
    sig = _signals.filter((pl.col("signal_date") >= start) & (pl.col("signal_date") <= end))
    ohlcv = _ohlcv.filter((pl.col("date") >= win_lo) & (pl.col("date") <= win_hi))

    overrides = dict(_BASE_OVERRIDES)
    for key, value in entry_ov.items():
        if key in overrides and value is not None:
            overrides[key] = value
    rs_min = entry_ov.get("rs_rank_min", _settings.backtest.rs_rank_min)
    max_spd = entry_ov.get("max_signals_per_day", _settings.backtest.max_signals_per_day)

    filt = diag.filter_signals(
        sig, settings=_settings, diagnostics=_diagnostics, sector_map=_sector_map,
        rs_rank_min=rs_min, rs_rank_max=_settings.backtest.rs_rank_max,
        ranking_mode=_settings.backtest.signal_ranking_mode,
        max_signals_per_day=max_spd,
        quality_profile=_settings.backtest.signal_quality_profile,
        quality_overrides=overrides,
    )
    eg = {d: v for d, v in _ENTRY_GATE.items() if start <= d <= win_hi}
    esm = {d: v for d, v in _ENTRY_SIZE_MULT.items() if start <= d <= win_hi}

    ex = {**_BASE_EXIT, **exit_ov}
    portfolio = {**_portfolio, **(portfolio_ov or {})}
    params = replace(_params, **(params_ov or {}))
    res = diag.simulate_portfolio_python(
        filt, ohlcv, params, portfolio, _sector_map,
        exit_config=ex if _settings.backtest.use_extended_exit_model else None,
        entry_gate=eg, entry_size_mult=esm,
        breakeven_activation_r=ex["breakeven_activation_r"],
        time_stop_days=ex["time_stop_days"],
        partial_profit_r=ex["partial_profit_r"],
        trail_pct_bull=ex["trail_pct_bull"],
        trail_pct_bear=ex["trail_pct_bear"],
        trail_widen_r=ex["trail_widen_r"],
        **(simulation_ov or {}),
    )
    m = _metrics(res["daily_equity"], start, end)
    tr = res["trades"]
    if "entry_date" in tr.columns and tr.height:
        n_trades = tr.filter(
            pl.col("entry_date").is_not_null()
            & (pl.col("entry_date") >= start) & (pl.col("entry_date") <= end)
        ).height
    else:
        n_trades = 0
    if m is not None:
        m["trades"] = n_trades
    return m


# ---------------------------------------------------------------------------
def parity():
    print("=== PARITY CHECK (base config, D+1 entry / trading-bar pending window) ===")
    m = run_config({}, {}, FULL_START, FULL_END)
    cash_reference = run_config(
        {}, {}, FULL_START, FULL_END, portfolio_ov={"cash_yield_apy": 0.06}
    )
    print(f"  CAGR={m['cagr']*100:.2f}%  MaxDD={m['mdd']*100:.2f}%  Calmar={m['calmar']:.2f}  trades={m['trades']}")
    print(
        "  committed strategy-only (cash yield 0%): "
        f"CAGR={m['cagr']*100:.2f}%  MaxDD={m['mdd']*100:.2f}%  "
        f"Calmar={m['calmar']:.2f}  trades={m['trades']}"
    )
    print(
        "  reference only (assumed idle-cash yield 6%; excluded from SEPA metrics): "
        f"CAGR={cash_reference['cagr']*100:.2f}%  "
        f"MaxDD={cash_reference['mdd']*100:.2f}%  "
        f"Calmar={cash_reference['calmar']:.2f}  trades={cash_reference['trades']}"
    )


def objective_value(entry_ov: dict, exit_ov: dict):
    calmars, info = [], []
    for (s, e) in IS_FOLDS:
        m = run_config(entry_ov, exit_ov, s, e)
        if m is None or m["trades"] < MIN_TRADES_PER_FOLD or np.isnan(m["calmar"]):
            return None, None
        calmars.append(m["calmar"]); info.append(m)
    score = float(np.mean(calmars) - LAMBDA * np.std(calmars))
    return score, {"fold_calmar": [round(c, 3) for c in calmars],
                   "fold_cagr": [round(i["cagr"] * 100, 1) for i in info],
                   "fold_trades": [i["trades"] for i in info],
                   "mean_calmar": round(float(np.mean(calmars)), 3),
                   "std_calmar": round(float(np.std(calmars)), 3)}


def _suggest(trial):
    entry_ov = {
        "rs_rank_min": trial.suggest_int("rs_rank_min", 55, 85, step=5),
        "vol_mult_min": trial.suggest_float("vol_mult_min", 1.0, 2.5, step=0.1),
        "voldry_min": trial.suggest_float("voldry_min", 0.0, 0.6, step=0.1),
        "max_signals_per_day": trial.suggest_int("max_signals_per_day", 2, 10),
    }
    exit_ov = {
        "breakeven_activation_r": trial.suggest_float("breakeven_activation_r", 1.0, 3.0, step=0.25),
        "partial_profit_r": trial.suggest_float("partial_profit_r", 2.0, 10.0, step=0.5),
        "trail_pct_bull": trial.suggest_float("trail_pct_bull", 0.15, 0.35, step=0.05),
        "trail_pct_bear": trial.suggest_float("trail_pct_bear", 0.10, 0.25, step=0.05),
        "time_stop_days": trial.suggest_categorical("time_stop_days", [None, 15, 25, 40]),
    }
    return entry_ov, exit_ov


def optimize(n_trials: int, study_name: str, db: str):
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def obj(trial):
        entry_ov, exit_ov = _suggest(trial)
        score, info = objective_value(entry_ov, exit_ov)
        if score is None:
            raise optuna.TrialPruned()
        for k, v in info.items():
            trial.set_user_attr(k, v)
        return score

    study = optuna.create_study(study_name=study_name, storage=db, load_if_exists=True,
                                direction="maximize")
    study.optimize(obj, n_trials=n_trials, show_progress_bar=True)
    print(f"\nBest score={study.best_value:.3f}  params={study.best_params}")
    print(f"  attrs={study.best_trial.user_attrs}")


def report(study_name: str, db: str, top_k: int):
    import optuna
    study = optuna.load_study(study_name=study_name, storage=db)
    completed = [t for t in study.trials if t.value is not None]
    completed.sort(key=lambda t: t.value, reverse=True)
    top = completed[:top_k]

    # Plateau pick: among top_k, choose the trial whose param-neighbors (other top
    # trials within normalized L1 tolerance) have the highest median score -> robust region.
    keys = list(top[0].params.keys())

    def norm(p):
        out = []
        for k in keys:
            v = p[k]
            if v is None:
                out.append(0.0)
            elif isinstance(v, bool):
                out.append(float(v))
            else:
                out.append(float(v))
        return np.array(out, float)

    # normalize each dim to [0,1] across top set
    M = np.array([norm(t.params) for t in top])
    rng = (M.max(0) - M.min(0)); rng[rng == 0] = 1.0
    Mn = (M - M.min(0)) / rng
    best_i, best_density = 0, -1e18
    TOL = 0.25
    for i in range(len(top)):
        d = np.abs(Mn - Mn[i]).sum(1)
        neigh = [top[j].value for j in range(len(top)) if d[j] <= TOL * len(keys)]
        med = float(np.median(neigh))
        if len(neigh) >= 3 and med > best_density:
            best_density, best_i = med, i
    pick = top[best_i]

    print("\n=== TOP TRIALS (in-sample fold score) ===")
    for r, t in enumerate(top[:10], 1):
        a = t.user_attrs
        print(f"#{r} score={t.value:.3f} meanCal={a.get('mean_calmar')} stdCal={a.get('std_calmar')} "
              f"foldCAGR={a.get('fold_cagr')} {t.params}")

    print(f"\n=== PLATEAU PICK (robust neighborhood, density={best_density:.3f}) ===")
    print(f"  params={pick.params}")
    print(f"  in-sample attrs={pick.user_attrs}")

    entry_ov = {k: pick.params[k] for k in ("rs_rank_min", "vol_mult_min", "voldry_min", "max_signals_per_day")}
    exit_ov = {k: pick.params[k] for k in ("breakeven_activation_r", "partial_profit_r",
                                           "trail_pct_bull", "trail_pct_bear", "time_stop_days")}

    print("\n=== HOLDOUT 2023-01-01..END (never optimized) — CURRENT vs CANDIDATE ===")
    cur = run_config({}, {}, HOLDOUT_START, FULL_END)
    cand = run_config(entry_ov, exit_ov, HOLDOUT_START, FULL_END)
    print(f"  CURRENT   CAGR={cur['cagr']*100:6.2f}%  MaxDD={cur['mdd']*100:6.2f}%  Calmar={cur['calmar']:.2f}  trades={cur['trades']}")
    print(f"  CANDIDATE CAGR={cand['cagr']*100:6.2f}%  MaxDD={cand['mdd']*100:6.2f}%  Calmar={cand['calmar']:.2f}  trades={cand['trades']}")

    print("\n=== FULL HISTORY 2010..END — CURRENT vs CANDIDATE ===")
    fcur = run_config({}, {}, FULL_START, FULL_END)
    fcand = run_config(entry_ov, exit_ov, FULL_START, FULL_END)
    print(f"  CURRENT   CAGR={fcur['cagr']*100:6.2f}%  MaxDD={fcur['mdd']*100:6.2f}%  Calmar={fcur['calmar']:.2f}  trades={fcur['trades']}")
    print(f"  CANDIDATE CAGR={fcand['cagr']*100:6.2f}%  MaxDD={fcand['mdd']*100:6.2f}%  Calmar={fcand['calmar']:.2f}  trades={fcand['trades']}")

    Path("best_joint_params.json").write_text(json.dumps(
        {"entry": entry_ov, "exit": exit_ov, "holdout_candidate": cand, "holdout_current": cur}, indent=2, default=str))
    print("\nWrote best_joint_params.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--parity", action="store_true")
    ap.add_argument("--trials", type=int, default=0)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--top-k", type=int, default=30)
    ap.add_argument("--study", default="joint_wf_v1")
    ap.add_argument("--db", default="sqlite:///optuna_joint_wf.db")
    a = ap.parse_args()
    if a.parity:
        parity()
    if a.trials > 0:
        optimize(a.trials, a.study, a.db)
    if a.report:
        report(a.study, a.db, a.top_k)
