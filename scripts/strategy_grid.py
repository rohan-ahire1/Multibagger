"""Run strategy CONFIG variants through the ONE honest engine (simulate_portfolio_python)
and rank by a consistent risk-adjusted metric. One OHLCV load, reused across the grid."""
from __future__ import annotations
import itertools, statistics
from datetime import date, timedelta

import polars as pl

from sepa_scanner.main import resolve_settings, _stores
from sepa_scanner.backtest.queries import get_backtest_signals, get_backtest_ohlcv
from sepa_scanner.backtest.models import BacktestParams, ExitMode
from sepa_scanner.backtest.simulation import simulate_portfolio_python, filter_signals

START, END = date(2010, 1, 1), date(2026, 3, 17)
settings = resolve_settings(None)
parquet_store, duckdb_store = _stores(settings, read_only=True)

raw_signals = get_backtest_signals(duckdb_store._connect(), START, END)
sm = getattr(settings.backtest, "skip_months", None)
if sm:
    raw_signals = raw_signals.filter(~pl.col("signal_date").dt.month().is_in(sm))

# One OHLCV load for the union of all signal symbols
all_syms = raw_signals["symbol"].unique().to_list()
ohlcv_end = END + timedelta(days=(settings.backtest.pending_days + settings.backtest.max_hold_days) * 2)
OHLCV = get_backtest_ohlcv(parquet_store, all_syms, START, ohlcv_end)
print(f"loaded OHLCV: {OHLCV.height} rows, {len(all_syms)} symbols\n")

PHASED_EXIT_CFG = {
    "breakeven_activation_r": settings.exit_strategy.breakeven_activation_r,
    "partial_profit_r": settings.exit_strategy.partial_profit_r,
    "trail_pct_bull": settings.exit_strategy.trail_pct_bull,
    "trail_pct_bear": settings.exit_strategy.trail_pct_bear,
    "trail_widen_r": getattr(settings.exit_strategy, "trail_widen_r", 3.0),
    "max_loss_pct": settings.exit_strategy.max_loss_pct,
    "absolute_max_loss_pct": settings.exit_strategy.absolute_max_loss_pct,
}

def metrics(daily_equity: pl.DataFrame):
    eq = daily_equity.sort("date")
    e = eq["equity"].to_list(); d = eq["date"].to_list()
    if len(e) < 2: return None
    years = (d[-1] - d[0]).days / 365.25
    cagr = (e[-1] / e[0]) ** (1 / years) - 1 if e[-1] > 0 else -1
    peak = e[0]; maxdd = 0.0
    for v in e:
        peak = max(peak, v); maxdd = max(maxdd, (peak - v) / peak)
    rets = eq["daily_return"].drop_nulls().to_list()
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0
    sharpe = (statistics.mean(rets) / sd) * (252 ** 0.5) if sd > 0 else 0.0
    calmar = cagr / maxdd if maxdd > 0 else 0.0
    avg_cash_pct = eq["cash"].mean() / e[0] * 100
    return dict(cagr=cagr * 100, maxdd=maxdd * 100, calmar=calmar,
               sharpe=sharpe, mult=e[-1] / e[0], cash=avg_cash_pct)

# Grid axes
EXIT_MODES = ["baseline_ema21", "baseline_ema10", "baseline_hard", "minervini_phased"]
RS_MINS = [55, 70, 80]
# concentration: (max_positions, max_name_pct, risk_per_trade_pct)
CONC = {
    "diffuse(20/20%)": (20, 0.20, 0.020),
    "concentrated(10/25%)": (10, 0.25, 0.035),
}

rows = []
for exit_mode, rs_min, (cname, (maxpos, namepct, risk)) in itertools.product(EXIT_MODES, RS_MINS, CONC.items()):
    sig = filter_signals(
        raw_signals, settings=settings,
        rs_rank_min=rs_min, rs_rank_max=settings.backtest.rs_rank_max,
        ranking_mode=settings.backtest.signal_ranking_mode,
        max_signals_per_day=settings.backtest.max_signals_per_day,
    )
    params = BacktestParams(
        pending_days=settings.backtest.pending_days,
        max_hold_days=settings.backtest.max_hold_days,
        target_r=settings.risk.target_r,
        exit_mode=ExitMode(exit_mode),
        friction_enabled=True,
    )
    portfolio = {
        "initial_capital": 1_000_000.0, "risk_per_trade_pct": risk,
        "max_positions": maxpos, "max_name_pct": namepct,
        "drawdown_half_pct": settings.portfolio.drawdown_half_pct,
        "drawdown_stop_pct": settings.portfolio.drawdown_stop_pct,
        "drawdown_close_pct": settings.portfolio.drawdown_close_pct,
    }
    exit_cfg = PHASED_EXIT_CFG if exit_mode == "minervini_phased" else None
    res = simulate_portfolio_python(sig, OHLCV, params, portfolio, {}, exit_config=exit_cfg)
    m = metrics(res["daily_equity"])
    if m is None: continue
    rows.append(dict(exit=exit_mode, rs=rs_min, conc=cname, **m,
                    trades=len(res["trades"])))
    print(f"  {exit_mode:18s} rs>={rs_min} {cname:20s} "
          f"CAGR {m['cagr']:6.2f}% DD {m['maxdd']:5.1f}% Calmar {m['calmar']:.2f} "
          f"Sharpe {m['sharpe']:.2f} cash {m['cash']:.0f}%")

print("\n================ RANKED BY CALMAR (CAGR / MaxDD) ================")
for r in sorted(rows, key=lambda x: x["calmar"], reverse=True):
    print(f"  Calmar {r['calmar']:.2f} | CAGR {r['cagr']:6.2f}% | DD {r['maxdd']:5.1f}% | "
          f"Sharpe {r['sharpe']:.2f} | {r['mult']:6.1f}x | cash {r['cash']:3.0f}% | "
          f"{r['exit']} rs>={r['rs']} {r['conc']} ({r['trades']} tr)")
