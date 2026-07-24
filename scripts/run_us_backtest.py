"""Run the SEPA engine on US (S&P 500) stocks — validation test.

Reuses the exact strategy logic (scan_batch: trend template + VCP + breakout +
RS gate) and the portfolio simulator. Only the data, benchmark (SPY), universe,
and sector map are swapped. Locked aggressive exit config is applied.
"""
from __future__ import annotations

import copy
import sys
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import argparse
from sepa_scanner.settings import load_settings
from sepa_scanner.strategy.signal_engine import scan_batch
from sepa_scanner.backtest.models import BacktestParams, ExitMode
import scripts.portfolio_diagnostics as diag

_ap = argparse.ArgumentParser()
_ap.add_argument("--ohlcv", default="data/us/ohlcv_all.parquet")
_ap.add_argument("--universe", default="data/reference/sp500_constituents.csv")
_ap.add_argument("--start", default="2010-01-04")
_ap.add_argument("--benchmarks", default="SPY")  # comma list of B&H comparisons present in ohlcv
_ap.add_argument("--min-price", default="10", dest="min_price")
_ap.add_argument("--min-adv", default="5000000", dest="min_adv")    # $5M avg daily turnover
_ap.add_argument("--min-vol", default="100000", dest="min_vol")
_ap.add_argument("--conservative", action="store_true", help="use conservative exits (breakeven 1.5R, partial 2R, time-stop 15d, trail 0.20)")
_AR = _ap.parse_args()
OHLCV = _ROOT / _AR.ohlcv
START, END = date.fromisoformat(_AR.start), date(2026, 5, 29)


def metrics(eq, dates):
    yrs = (dates[-1] - dates[0]).days / 365.25
    cagr = (eq[-1] / eq[0]) ** (1 / max(yrs, 1e-6)) - 1
    peak = -1e18; mdd = 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return cagr, mdd, (cagr / abs(mdd) if mdd < 0 else float("nan"))


def main() -> None:
    settings = copy.deepcopy(load_settings())
    settings.market.benchmark_symbol = "SPY"   # US benchmark for RS / regime
    settings.india_filter = None
    # US-appropriate liquidity floors (config defaults are rupee-calibrated:
    # $50 min price + $50M turnover would screen out most US small caps).
    settings.universe.min_price_inr = float(_AR.min_price)
    settings.universe.min_adv50_inr = float(_AR.min_adv)
    settings.universe.min_avg_volume_shares = int(_AR.min_vol)

    ohlcv = pl.read_parquet(OHLCV).filter(pl.col("date") <= END)
    sectors = pl.read_csv(_ROOT / _AR.universe)
    sector_map = {r["symbol"]: r["sector"] for r in sectors.iter_rows(named=True)}

    all_dates = sorted(d for d in ohlcv["date"].unique().to_list() if START <= d <= END)
    print(f"US universe: {ohlcv['symbol'].n_unique()} symbols | dates {all_dates[0]}..{all_dates[-1]} ({len(all_dates)})")
    print("Scanning (trend template + VCP + breakout + RS gate)...")

    sig_frames = []
    for sd, result, err in scan_batch(ohlcv, settings, all_dates):
        if result is None or result.signals.is_empty():
            continue
        bo = result.signals.filter(pl.col("signal_type") == "BREAKOUT")
        if bo.height:
            sig_frames.append(bo)
    if not sig_frames:
        print("No breakout signals generated."); return
    signals = pl.concat(sig_frames, how="diagonal_relaxed").sort(["signal_date", "symbol"])
    print(f"BREAKOUT signals: {signals.height} over {signals['signal_date'].n_unique()} days")

    if _AR.conservative:
        settings.exit_strategy.breakeven_activation_r = 1.5
        settings.exit_strategy.partial_profit_r = 2.0
        settings.exit_strategy.time_stop_days = 15
        settings.exit_strategy.trail_pct_bull = 0.20

    # Portfolio params: US-appropriate friction proxy on.
    es = settings.exit_strategy
    params = BacktestParams(
        pending_days=settings.backtest.pending_days,
        max_hold_days=max(settings.backtest.max_hold_days, 90),
        target_r=settings.risk.target_r,
        exit_mode=ExitMode(settings.backtest.exit_mode),
        baseline_tighten_r=settings.backtest.baseline_tighten_r,
        trail_activation_r=es.trail_activation_r,
        trail_pct=es.trail_pct,
        dead_money_days=es.dead_money_days,
        dead_money_extend_days=es.dead_money_extend_days,
        dead_money_min_open_r=es.dead_money_min_open_r,
        dead_money_min_mfe_r=es.dead_money_min_mfe_r,
        dead_money_extend_to_breakeven=es.dead_money_extend_to_breakeven,
        friction_enabled=True,
    )
    portfolio = {
        "initial_capital": settings.portfolio.initial_capital,
        "risk_per_trade_pct": settings.portfolio.risk_per_trade_pct,
        "max_positions": settings.portfolio.max_positions,
        "max_sector_positions": settings.portfolio.max_sector_positions,
        "max_name_pct": settings.portfolio.max_name_pct,
        "max_sector_pct": settings.portfolio.max_sector_pct,
        "max_heat_pct": settings.portfolio.max_heat_pct,
        "drawdown_half_pct": settings.portfolio.drawdown_half_pct,
        "drawdown_stop_pct": settings.portfolio.drawdown_stop_pct,
        "drawdown_close_pct": settings.portfolio.drawdown_close_pct,
        "regime_gate_enabled": settings.portfolio.regime_gate_enabled,
        "regime_high_volatility_threshold": settings.portfolio.regime_high_volatility_threshold,
        "regime_cooldown_days": settings.portfolio.regime_cooldown_days,
        "breadth_soft_floor_pct": settings.portfolio.breadth_soft_floor_pct,
        "breadth_soft_size_mult": settings.portfolio.breadth_soft_size_mult,
        "pyramid_max_adds": settings.portfolio.pyramid_max_adds,
        "pyramid_trigger_r": settings.portfolio.pyramid_trigger_r,
        "pyramid_add_interval_r": settings.portfolio.pyramid_add_interval_r,
        "pyramid_risk_frac": settings.portfolio.pyramid_risk_frac,
    }
    res = diag.simulate_portfolio_python(
        signals, ohlcv, params, portfolio, sector_map,
        exit_config=diag.resolve_exit_config(settings),
    )

    de = res["daily_equity"].sort("date")
    eq = de["equity"].to_numpy(); dts = de["date"].to_list()
    c, dd, cal = metrics(eq, dts)
    trades = res["trades"].filter(pl.col("r_multiple").is_not_null())

    print(f"\n================ SEPA on US ({_AR.universe.split('/')[-1]}) ================")
    print(f"  period {dts[0]} -> {dts[-1]}")
    rr = trades["r_multiple"].to_numpy()
    print(f"  STRATEGY   CAGR={c*100:6.2f}%  MaxDD={dd*100:7.2f}%  Calmar={cal:.2f}  trades={trades.height}  end=${eq[-1]:,.0f}")
    print(f"             per-trade: win%={np.mean(rr>0)*100:.1f}  avgR={np.mean(rr):+.3f}  medR={np.median(rr):+.3f}")
    for bsym in _AR.benchmarks.split(","):
        bsym = bsym.strip()
        bdf = ohlcv.filter((pl.col("symbol") == bsym) & (pl.col("date") >= dts[0])).sort("date")
        if bdf.height < 20:
            continue
        bc, bdd, bcal = metrics(bdf["close"].to_numpy(), bdf["date"].to_list())
        print(f"  {bsym:<4} B&H    CAGR={bc*100:6.2f}%  MaxDD={bdd*100:7.2f}%  Calmar={bcal:.2f}")
    print("\n  Top trades by R:")
    for r in trades.sort("r_multiple", descending=True).head(8).iter_rows(named=True):
        print(f"    {r['symbol']:<7} {str(r['entry_date'])}  {r.get('return_pct',0):+.0f}%  {r['r_multiple']:.1f}R")


if __name__ == "__main__":
    main()
