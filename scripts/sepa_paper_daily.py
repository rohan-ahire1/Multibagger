"""SEPA daily paper-trading runner (stateless re-simulation).

Each evening this re-runs the validated SEPA portfolio engine over full history
to the latest available bar. The engine's open positions and equity ARE the
paper book -- no separate exit state machine to drift from the backtest. Dhan
friction is already applied inside the engine (friction_enabled=True).

Outputs (git-ignored under data/live/sepa_paper/):
  equity.csv      full daily equity curve (authoritative, overwritten each run)
  trades.csv      all closed paper trades (overwritten each run)
  positions.json  current open positions + headline snapshot

Usage:
  .venv/bin/python scripts/sepa_paper_daily.py [--update] [--start 2010-01-04]

  --update  run the data update + scan pipeline first (network). Default: skip
            and use whatever signals/data are already current.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import polars as pl

import scripts.portfolio_diagnostics as diag
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore
from sepa_scanner.strategy.ranking import load_sector_map

OUT_DIR = _ROOT / "data" / "live" / "sepa_paper"
SECTOR_MAP_PATH = _ROOT / "data" / "reference" / "full_sector_map.csv"
FALLBACK_MAP = _ROOT / "data" / "reference" / "nifty500_constituents.csv"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SEPA daily paper-trading runner.")
    p.add_argument("--start", default="2010-01-04", help="Backtest inception date")
    p.add_argument("--update", action="store_true", help="Run data update + scan first (network)")
    return p.parse_args()


def money(v: float) -> str:
    return f"₹{v:,.0f}"


def main() -> None:
    args = parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()

    if args.update:
        from sepa_scanner.main import run_scan_command, run_update_command

        latest, _ = run_update_command(config_dir=None, days=5, target_date=None, force=False)
        run_scan_command(config_dir=None, scan_date=latest, export_parquet=True,
                         export_csv=False, no_telegram=True)

    latest_date = ParquetOhlcvStore(_ROOT / "data" / "yf_adjusted").latest_date()
    if latest_date is None:
        print("No OHLCV data found.")
        return

    inputs, signals, ohlcv = diag.load_inputs(start, latest_date)
    sector_map = load_sector_map(SECTOR_MAP_PATH if SECTOR_MAP_PATH.exists() else FALLBACK_MAP)
    settings = inputs["settings"]

    # Canonical path: same RS>=70 + quality + per-day-cap filter the terminal
    # backtest uses, so the paper book matches the validated strategy exactly.
    signals = diag.filter_signals(
        signals, settings=settings, diagnostics=inputs["diagnostics"], sector_map=sector_map,
        rs_rank_min=settings.backtest.rs_rank_min, rs_rank_max=settings.backtest.rs_rank_max,
        ranking_mode=settings.backtest.signal_ranking_mode,
        max_signals_per_day=settings.backtest.max_signals_per_day,
        quality_profile=settings.backtest.signal_quality_profile,
        quality_overrides=diag.resolve_signal_quality_overrides(settings),
    )
    es = settings.exit_strategy
    result = diag.simulate_portfolio_python(
        signals, ohlcv, inputs["params"], inputs["portfolio"], sector_map,
        exit_config=None,
        breakeven_activation_r=es.breakeven_activation_r,
        partial_profit_r=es.partial_profit_r,
        trail_pct_bull=es.trail_pct_bull,
        trail_pct_bear=es.trail_pct_bear,
        time_stop_days=es.time_stop_days,
    )

    de = result["daily_equity"].sort("date")
    trades = result["trades"]
    open_pos = result["open_positions"]

    eq = de["equity"].to_list()
    equity_now = eq[-1]
    peak = max(eq)
    run_peak = -1e18
    dd = 0.0
    for v in eq:
        run_peak = max(run_peak, v)
        dd = min(dd, v / run_peak - 1.0)
    cur_dd = equity_now / peak - 1.0
    start_eq = float(inputs["portfolio"]["initial_capital"])

    # latest close per symbol for mark-to-market
    last_close = {
        r["symbol"]: r["close"]
        for r in ohlcv.filter(pl.col("date") == latest_date).select(["symbol", "close"]).to_dicts()
    }

    # enrich open positions with unrealized P&L
    holdings = []
    invested = 0.0
    for p in open_pos:
        mark = last_close.get(p["symbol"], p["entry_price"])
        mkt_val = mark * p["shares"]
        unreal = (mark - p["entry_price"]) * p["shares"] + p["realized_pnl"]
        r_mult = (mark - p["entry_price"]) / p["initial_risk"] if p["initial_risk"] else 0.0
        invested += mkt_val
        holdings.append({
            **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items()},
            "mark": mark, "market_value": mkt_val, "unrealized_pnl": unreal,
            "open_r": round(r_mult, 2),
            "holding_days": (latest_date - p["entry_date"]).days,
        })
    holdings.sort(key=lambda h: h["unrealized_pnl"], reverse=True)
    cash = equity_now - invested

    # Genuine exits only: the engine also writes status="open" / skipped / not_filled
    # rows (current-holding marks and non-fills) which must not count as exits.
    _NON_EXIT = ["open", "not_filled", "missing_data", "incomplete_data", "skipped_portfolio"]
    if "exit_date" in trades.columns and trades.height:
        today_exits = trades.filter(
            (pl.col("exit_date") == latest_date)
            & pl.col("entry_date").is_not_null()
            & ~pl.col("status").is_in(_NON_EXIT)
            & ~pl.col("status").str.starts_with("skipped")
        )
    else:
        today_exits = trades.head(0)
    today_entries = [h for h in holdings if h["entry_date"] == latest_date.isoformat()]

    # --- persist ---
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    de.write_csv(OUT_DIR / "equity.csv")
    if trades.height:
        trades.write_csv(OUT_DIR / "trades.csv")
    (OUT_DIR / "positions.json").write_text(json.dumps({
        "as_of": latest_date.isoformat(),
        "equity": equity_now, "cash": cash, "invested": invested,
        "open_positions": len(holdings), "current_drawdown_pct": round(cur_dd * 100, 2),
        "holdings": holdings,
    }, indent=2, default=str), encoding="utf-8")

    # --- report ---
    years = max((de["date"].to_list()[-1] - de["date"].to_list()[0]).days / 365.25, 1e-6)
    cagr = (equity_now / start_eq) ** (1.0 / years) - 1.0
    L = [
        f"\U0001F4C8 SEPA Paper Book — as of {latest_date.isoformat()}",
        "",
        f"Equity: {money(equity_now)}  ({equity_now / start_eq:.2f}x from {money(start_eq)})",
        f"Cash: {money(cash)} | Invested: {money(invested)} | Positions: {len(holdings)}/{inputs['portfolio']['max_positions']}",
        f"CAGR: {cagr * 100:.1f}% | Current DD: {cur_dd * 100:.1f}% | Worst DD: {dd * 100:.1f}%",
        "",
    ]
    if today_entries:
        L.append("\U0001F7E2 ENTERED TODAY")
        for h in today_entries:
            L.append(f"  • {h['symbol']} @ {money(h['entry_price'])} | {h['shares']:.0f} sh | stop {money(h['active_stop'])}")
    if today_exits.height:
        L.append("\U0001F534 EXITED TODAY")
        for t in today_exits.to_dicts():
            pnl = t.get("pnl") or ((t.get("exit_price", 0) - t.get("entry_price", 0)) * t.get("shares", 0))
            L.append(f"  • {t['symbol']} @ {money(t.get('exit_price', 0))} | {t.get('status', '')} | P&L {money(pnl)}")
    if not today_entries and not today_exits.height:
        L.append("⚪ No fills or exits today.")
    L.append("")
    L.append("\U0001F4CB HOLDINGS (by unrealized P&L)")
    for h in holdings[:25]:
        sign = "+" if h["unrealized_pnl"] >= 0 else ""
        L.append(f"  • {h['symbol']:14s} {h['open_r']:+.1f}R | {sign}{money(h['unrealized_pnl'])} | {h['holding_days']}d | mark {money(h['mark'])}")
    if len(holdings) > 25:
        L.append(f"  … and {len(holdings) - 25} more")
    print("\n".join(L))
    print(f"\nWrote snapshot to {OUT_DIR}/")


if __name__ == "__main__":
    main()
