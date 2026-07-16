"""Reconstruct the REAL portfolio equity-curve CAGR / max-DD% from simulate_portfolio_python,
contrasting with the serial-R-compounding headline CAGR the CLI prints."""
from __future__ import annotations
import sys
from datetime import date, timedelta

import polars as pl

from sepa_scanner.main import resolve_settings, _stores
from sepa_scanner.backtest.queries import get_backtest_signals, get_backtest_ohlcv
from sepa_scanner.backtest.models import BacktestParams, ExitMode
from sepa_scanner.backtest.simulation import simulate_portfolio_python, filter_signals

start, end = date(2010, 1, 1), date(2026, 3, 17)
settings = resolve_settings(None)
parquet_store, duckdb_store = _stores(settings, read_only=True)

signals = get_backtest_signals(duckdb_store._connect(), start, end)
sm = getattr(settings.backtest, "skip_months", None)
if sm:
    signals = signals.filter(~pl.col("signal_date").dt.month().is_in(sm))

# Apply same filter the terminal uses
signals = filter_signals(
    signals, settings=settings,
    rs_rank_min=settings.backtest.rs_rank_min,
    rs_rank_max=settings.backtest.rs_rank_max,
    ranking_mode=settings.backtest.signal_ranking_mode,
    max_signals_per_day=settings.backtest.max_signals_per_day,
)

syms = signals["symbol"].unique().to_list()
ohlcv_end = end + timedelta(days=(settings.backtest.pending_days + settings.backtest.max_hold_days) * 2)
ohlcv = get_backtest_ohlcv(parquet_store, syms, start, ohlcv_end)

params = BacktestParams(
    pending_days=settings.backtest.pending_days,
    max_hold_days=settings.backtest.max_hold_days,
    target_r=settings.risk.target_r,
    exit_mode=ExitMode(settings.backtest.exit_mode),
    friction_enabled=True,
)
portfolio = {
    "initial_capital": settings.portfolio.initial_capital,
    "risk_per_trade_pct": settings.portfolio.risk_per_trade_pct,
    "max_positions": settings.portfolio.max_positions,
    "max_name_pct": settings.portfolio.max_name_pct,
    "drawdown_half_pct": settings.portfolio.drawdown_half_pct,
    "drawdown_stop_pct": settings.portfolio.drawdown_stop_pct,
    "drawdown_close_pct": settings.portfolio.drawdown_close_pct,
}

res = simulate_portfolio_python(signals, ohlcv, params, portfolio, {})
eq = res["daily_equity"].sort("date")
e = eq["equity"].to_list()
d = eq["date"].to_list()
init, final = e[0], e[-1]
years = (d[-1] - d[0]).days / 365.25
cagr = (final / init) ** (1 / years) - 1

peak = e[0]; maxdd = 0.0
for v in e:
    peak = max(peak, v)
    maxdd = max(maxdd, (peak - v) / peak)

rets = eq["daily_return"].drop_nulls().to_list()
import statistics
mu, sd = statistics.mean(rets), statistics.pstdev(rets)
sharpe = (mu / sd) * (252 ** 0.5) if sd > 0 else float("nan")

# pct invested (cash drag)
avg_cash = eq["cash"].mean()
print(f"signals_after_filter: {signals.height}")
print(f"trades_closed:        {len(res['trades'])}")
print(f"years:                {years:.2f}")
print(f"REAL equity CAGR:     {cagr*100:.2f}%")
print(f"REAL max drawdown:    {maxdd*100:.2f}%")
print(f"final equity mult:    {final/init:.2f}x")
print(f"daily Sharpe(252):    {sharpe:.2f}")
print(f"avg cash (of {init:.0f}): {avg_cash:.0f}  -> avg ~{avg_cash/init*100:.0f}% in cash")
