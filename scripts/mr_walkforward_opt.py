"""Walk-forward optimization of the mean-reversion exit family.

Rolling 4y in-sample / 2y out-of-sample folds. In each fold the exit
parameters (stop_atr_mult x max_hold, with the mean-reversion exit removed:
exit_mode="none" -> stop + time exit only) are optimized on in-sample data by
net-of-cost profit factor, then applied unchanged to the next out-of-sample
window. No look-ahead: OOS windows are never used for selection.

Reports per-fold chosen params + OOS metrics, the pooled out-of-sample edge
(the honest result), and a same-process control on the broken sma3 exit.

Usage:
    .venv/bin/python scripts/mr_walkforward_opt.py [--cost 0.003] [--slippage 5]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import polars as pl

from sepa_scanner.strategy.ranking import load_sector_map
from strategies.mean_reversion.exit_engine import MRExitConfig
from strategies.mean_reversion.signal_engine import scan_mean_reversion
from strategies.mean_reversion.simulation import simulate_mr_portfolio
from strategies.mean_reversion.universe import load_nifty100_universe
from strategies.mean_reversion.validation import load_mr_config, load_ohlcv

FULL_START, FULL_END = date(2010, 1, 4), date(2026, 5, 29)

# Rolling folds: (in-sample start, in-sample end, oos start, oos end)
FOLDS = [
    (date(2010, 1, 1), date(2013, 12, 31), date(2014, 1, 1), date(2015, 12, 31)),
    (date(2012, 1, 1), date(2015, 12, 31), date(2016, 1, 1), date(2017, 12, 31)),
    (date(2014, 1, 1), date(2017, 12, 31), date(2018, 1, 1), date(2019, 12, 31)),
    (date(2016, 1, 1), date(2019, 12, 31), date(2020, 1, 1), date(2021, 12, 31)),
    (date(2018, 1, 1), date(2021, 12, 31), date(2022, 1, 1), date(2023, 12, 31)),
    (date(2020, 1, 1), date(2023, 12, 31), date(2024, 1, 1), date(2025, 12, 31)),
    (date(2022, 1, 1), date(2025, 12, 31), date(2026, 1, 1), date(2026, 5, 29)),
]

STOP_GRID = [3.0, 4.0, 5.0, 6.0]
HOLD_GRID = [15, 20, 25, 30, 40]
MIN_FILLS_IS = 50


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Walk-forward optimize the MR exit family.")
    p.add_argument("--cost", type=float, default=0.003, help="Round-trip cost (decimal)")
    p.add_argument("--slippage", type=float, default=5.0, help="Slippage bps each side")
    p.add_argument("--output-dir", type=Path, default=Path("reports/mean_reversion/walkforward"))
    return p.parse_args()


def build() -> tuple[dict[str, Any], pl.DataFrame, dict[str, str], set[str]]:
    cfg = load_mr_config(Path("strategies/mean_reversion/config.yaml"))
    universe = load_nifty100_universe()
    sector_map = load_sector_map(Path(cfg["data"]["sector_map_path"]))
    ohlcv = load_ohlcv(cfg, FULL_START, FULL_END, universe).filter(pl.col("date") <= FULL_END)
    return cfg, ohlcv, sector_map, universe


def scan(ohlcv, cfg, universe, stop_mult: float) -> pl.DataFrame:
    return scan_mean_reversion(
        ohlcv, rsi_period=int(cfg["entry"]["rsi_period"]),
        rsi_threshold=float(cfg["entry"]["rsi_threshold"]),
        sma_trend_period=int(cfg["entry"]["sma_trend_period"]),
        min_adv_inr=float(cfg["entry"]["min_adv_inr"]),
        min_price=float(cfg["entry"]["min_price_inr"]),
        stop_atr_mult=stop_mult, universe_symbols=universe,
    )


def simulate(signals, ohlcv, cfg, sector_map, stop_mult, exit_mode, max_hold,
             start, end, cost, slip) -> pl.DataFrame:
    sigs = signals.filter((pl.col("signal_date") >= start) & (pl.col("signal_date") <= end))
    ec = MRExitConfig(exit_mode=exit_mode, time_stop_days=max_hold,
                      stop_atr_mult=stop_mult, max_hold_days=max_hold)
    res = simulate_mr_portfolio(
        sigs, ohlcv.filter(pl.col("date") <= end), start_date=start, end_date=end,
        max_positions=int(cfg["portfolio"]["max_positions"]),
        max_sector_positions=int(cfg["portfolio"]["max_sector_positions"]),
        initial_capital=float(cfg["portfolio"]["initial_capital"]),
        risk_per_trade_pct=0.01, exit_config=ec, sector_map=sector_map,
        round_trip_cost_pct=cost, exit_execution="next_open", slippage_bps=slip,
    )
    return res["trades"], res["daily_equity"]


def net_metrics(trades: pl.DataFrame, cost: float) -> dict[str, float]:
    if trades.is_empty():
        return {"n": 0, "win": 0.0, "avg_net": 0.0, "pf": 0.0}
    t = trades.with_columns((pl.col("pnl") - cost * pl.col("shares") * pl.col("entry_price")).alias("net"))
    pos = float(t.filter(pl.col("net") > 0)["net"].sum())
    neg = abs(float(t.filter(pl.col("net") <= 0)["net"].sum()))
    n = t.height
    return {
        "n": n,
        "win": 100.0 * t.filter(pl.col("net") > 0).height / n,
        "avg_net": float((t["return_pct"] - cost * 100.0).mean()),
        "pf": (pos / neg) if neg > 0 else float("inf"),
    }


def cagr_dd(daily_equity: pl.DataFrame) -> tuple[float, float]:
    if daily_equity.is_empty():
        return 0.0, 0.0
    eq = daily_equity["equity"].to_list()
    years = max(len(eq) / 252.0, 1e-6)
    cagr = ((eq[-1] / eq[0]) ** (1.0 / years) - 1.0) * 100.0 if eq[0] > 0 else 0.0
    return cagr, min(daily_equity["drawdown_pct"].to_list())


def main() -> None:
    args = parse_args()
    cfg, ohlcv, sector_map, universe = build()
    print(f"Loaded {ohlcv.height} bars. Scanning signals for {len(STOP_GRID)} stop values...")
    sig_cache = {sm: scan(ohlcv, cfg, universe, sm) for sm in STOP_GRID}
    # sma3 baseline uses config stop (3.0) signals.
    base_sig = sig_cache[3.0]

    fold_rows: list[dict[str, Any]] = []
    oos_opt_trades: list[pl.DataFrame] = []
    oos_base_trades: list[pl.DataFrame] = []

    for i, (is_s, is_e, oos_s, oos_e) in enumerate(FOLDS, 1):
        # --- optimize on in-sample by net PF ---
        best = None
        for sm in STOP_GRID:
            for mh in HOLD_GRID:
                tr, _ = simulate(sig_cache[sm], ohlcv, cfg, sector_map, sm, "none", mh,
                                 is_s, is_e, args.cost, args.slippage)
                m = net_metrics(tr, args.cost)
                if m["n"] < MIN_FILLS_IS:
                    continue
                key = (m["pf"], m["avg_net"])
                if best is None or key > best["key"]:
                    best = {"key": key, "stop": sm, "hold": mh, "is": m}
        if best is None:
            continue

        # --- apply chosen params to OOS ---
        sm, mh = best["stop"], best["hold"]
        oos_tr, oos_eq = simulate(sig_cache[sm], ohlcv, cfg, sector_map, sm, "none", mh,
                                  oos_s, oos_e, args.cost, args.slippage)
        oos_m = net_metrics(oos_tr, args.cost)
        oos_cagr, oos_dd = cagr_dd(oos_eq)
        oos_opt_trades.append(oos_tr)

        # --- control: broken sma3 exit on same OOS ---
        base_tr, base_eq = simulate(base_sig, ohlcv, cfg, sector_map, 3.0, "sma3", 20,
                                    oos_s, oos_e, args.cost, args.slippage)
        base_m = net_metrics(base_tr, args.cost)
        base_cagr, _ = cagr_dd(base_eq)
        oos_base_trades.append(base_tr)

        fold_rows.append({
            "fold": i, "OOS": f"{oos_s.year}-{oos_e.year}",
            "pick": f"s{sm:.0f}/h{mh}", "IS_pf": round(best["is"]["pf"], 2),
            "OOS_n": oos_m["n"], "OOS_pf": round(oos_m["pf"], 2),
            "OOS_avg%": round(oos_m["avg_net"], 2), "OOS_CAGR%": round(oos_cagr, 1),
            "OOS_maxDD%": round(oos_dd, 1), "base_pf": round(base_m["pf"], 2),
            "base_CAGR%": round(base_cagr, 1),
        })

    print("\n=== Per-fold walk-forward (params chosen IS, measured OOS) ===")
    print(pl.DataFrame(fold_rows).to_pandas().to_string(index=False))

    pooled_opt = net_metrics(pl.concat(oos_opt_trades), args.cost) if oos_opt_trades else {}
    pooled_base = net_metrics(pl.concat(oos_base_trades), args.cost) if oos_base_trades else {}
    print("\n=== Pooled OUT-OF-SAMPLE edge (the honest result) ===")
    print(f"  WFO-optimized exit: n={pooled_opt['n']}  win={pooled_opt['win']:.1f}%  "
          f"avg_net={pooled_opt['avg_net']:.3f}%  PF={pooled_opt['pf']:.3f}")
    print(f"  sma3 control      : n={pooled_base['n']}  win={pooled_base['win']:.1f}%  "
          f"avg_net={pooled_base['avg_net']:.3f}%  PF={pooled_base['pf']:.3f}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(fold_rows).write_csv(args.output_dir / "wfo_folds.csv")
    print(f"\nWrote {args.output_dir / 'wfo_folds.csv'}")


if __name__ == "__main__":
    main()
