from __future__ import annotations

import json
from dataclasses import asdict, fields, is_dataclass
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sepa_scanner.api.deps import get_duckdb_store, get_parquet_store, get_settings
from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.metrics import build_equity_curve, summarize_backtest
from sepa_scanner.backtest.signal_analysis import analyze_post_entry_returns, analyze_signal_quality
from sepa_scanner.backtest.signal_scorecard import compute_scorecard
from sepa_scanner.backtest.models import BacktestParams, BacktestTrade, ExitMode
from sepa_scanner.backtest.queries import get_backtest_ohlcv, get_backtest_signals
from sepa_scanner.settings import AppSettings
from sepa_scanner.backtest.portfolio import PortfolioManager
from sepa_scanner.backtest.regime_gate import compute_regime_gate, gate_lookup
from sepa_scanner.storage.duckdb_store import DuckDbStore
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class BacktestRunRequest(BaseModel):
    start_date: date
    end_date: date
    exit_mode: str = ExitMode.MINERVINI_PHASED.value
    target_r: float | None = None
    # None => fall back to config.yaml (settings.backtest) so the UI defaults
    # track the single source of truth instead of hardcoded copies.
    pending_days: int | None = None
    max_hold_days: int | None = None
    # Exit params are deliberately NOT exposed here. The portfolio sim sources
    # them from config.yaml (exit_strategy) via exit_config — single source of
    # truth. The old per-request trail/dead-money sliders were retired.
    exit_engine: str = "minervini_phased"
    use_portfolio: bool = True


def _cache_path(settings: AppSettings) -> Path:
    return settings.runtime.data_dir / "backtest" / "latest.json"


def _serialize_trade(trade) -> dict:
    d = asdict(trade)
    for key in ("signal_date", "entry_date", "exit_date", "partial_exit_date"):
        val = d.get(key)
        if val is not None:
            d[key] = val.isoformat()
    d.pop("mae_price", None)
    d.pop("mfe_price", None)
    return d


_TRADE_FIELDS = {f.name for f in fields(BacktestTrade)}


def _trades_from_frame(frame) -> list[BacktestTrade]:
    """Build BacktestTrade rows from the canonical engine's trades frame.

    The engine's column names already match the dataclass, apart from
    `giveback_r` / `peak_gain_capture_pct` / `initial_risk_price`, which the UI
    payload does not use. Unknown columns are dropped rather than passed
    through, so an engine-side column addition cannot break the endpoint.
    """
    if frame is None or getattr(frame, "is_empty", lambda: True)():
        return []
    return [
        BacktestTrade(**{k: v for k, v in row.items() if k in _TRADE_FIELDS})
        for row in frame.to_dicts()
    ]


def _settings_object_dict(value) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


@router.post("/run")
def run_backtest(
    req: BacktestRunRequest,
    settings: AppSettings = Depends(get_settings),
    store: DuckDbStore = Depends(get_duckdb_store),
    parquet: ParquetOhlcvStore = Depends(get_parquet_store),
) -> dict:
    if req.exit_engine == "minervini_phased":
        exit_mode = ExitMode.MINERVINI_PHASED
    elif req.exit_engine == "advanced_sepa":
        exit_mode = ExitMode.ADVANCED_SEPA
    else:
        exit_mode = ExitMode(req.exit_mode)

    # Single source of truth: fall back to config.yaml (settings.backtest) when
    # the request doesn't override pending_days / max_hold_days.
    pending_days = req.pending_days if req.pending_days is not None else settings.backtest.pending_days
    max_hold_days = req.max_hold_days if req.max_hold_days is not None else settings.backtest.max_hold_days

    # Exit params come from config (exit_strategy) via exit_config in the sim;
    # BacktestParams keeps its own defaults for the unused trail/dead-money fields.
    params = BacktestParams(
        pending_days=pending_days,
        max_hold_days=max_hold_days,
        target_r=req.target_r,
        exit_mode=exit_mode,
        baseline_tighten_r=getattr(settings.backtest, "baseline_tighten_r", 7.0),
        stop_trigger_mode=getattr(settings.backtest, "stop_trigger_mode", "close_only"),
        signal_exit_execution=getattr(
            settings.backtest, "signal_exit_execution", "same_close"
        ),
        slippage_bps=getattr(settings.backtest, "slippage_bps", 10.0),
        stagnation_exit_bars=getattr(
            settings.exit_strategy, "stagnation_exit_bars", None
        ),
        stagnation_max_mfe_r=getattr(
            settings.exit_strategy, "stagnation_max_mfe_r", 0.40
        ),
        stagnation_max_open_r=getattr(
            settings.exit_strategy, "stagnation_max_open_r", 0.0
        ),
        friction_enabled=True,
    )
    
    pm = None
    entry_gate = None
    if req.use_portfolio:
        pm = PortfolioManager(
            initial_capital=settings.portfolio.initial_capital,
            risk_per_trade_pct=settings.portfolio.risk_per_trade_pct,
            max_positions=settings.portfolio.max_positions,
            max_sector_positions=settings.portfolio.max_sector_positions,
            max_sector_pct=settings.portfolio.max_sector_pct,
            max_heat_pct=settings.portfolio.max_heat_pct,
            drawdown_half_pct=settings.portfolio.drawdown_half_pct,
            drawdown_stop_pct=settings.portfolio.drawdown_stop_pct,
            drawdown_close_pct=settings.portfolio.drawdown_close_pct,
        )
        if settings.portfolio.regime_gate_enabled:
            regime_gate = compute_regime_gate(
                parquet,
                benchmark_symbol=settings.market.benchmark_symbol,
                start_date=req.start_date,
                end_date=req.end_date,
                breadth_weak_pct=settings.market_filter.breadth_weak_pct,
                high_volatility_threshold=settings.portfolio.regime_high_volatility_threshold,
            )
            entry_gate = gate_lookup(regime_gate)

    store.initialize()
    conn = store._connect()
    signals_df = get_backtest_signals(conn, req.start_date, req.end_date)
    if signals_df.is_empty():
        raise HTTPException(status_code=404, detail="No breakout signals in date range")

    # SNIPER FILTER: Apply the same ranking used in the 75x Portfolio Sim
    # This filters out the 'junk' signals that were dragging the UI result down to 3R
    constituent_csv_path = settings.runtime.data_dir / "reference" / "nifty500_constituents.csv"
    from sepa_scanner.strategy.ranking import load_sector_map
    from sepa_scanner.backtest import filter_signals

    sector_map = load_sector_map(constituent_csv_path)

    signals_df = filter_signals(
        signals_df,
        settings=settings,
        sector_map=sector_map,
        rs_rank_min=settings.backtest.rs_rank_min or 40.0,
        rs_rank_max=settings.backtest.rs_rank_max or 99.5,
        ranking_mode=req.exit_engine if req.exit_engine == "sector_alpha" else settings.backtest.signal_ranking_mode,
        max_signals_per_day=settings.backtest.max_signals_per_day or 1, # Only the best of the day
    )

    if signals_df.is_empty():
        raise HTTPException(status_code=404, detail="No high-quality signals found in range")

    symbols = signals_df["symbol"].unique().to_list()
    extended_end = req.end_date + timedelta(days=int(max_hold_days * 1.5))
    ohlcv_df = get_backtest_ohlcv(parquet, symbols, req.start_date, extended_end)

    # UI now runs the Full Portfolio Simulation Engine (75x Logic)
    from sepa_scanner.backtest import simulate_portfolio_python, build_entry_controls, compute_regime_inputs

    # Setup Portfolio Params
    portfolio_cfg = {
        "initial_capital": settings.portfolio.initial_capital,
        "risk_per_trade_pct": settings.portfolio.risk_per_trade_pct,
        "max_positions": settings.portfolio.max_positions,
        "max_sector_positions": settings.portfolio.max_sector_positions,
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

    benchmark_df = get_backtest_ohlcv(parquet, [settings.market.benchmark_symbol], req.start_date, req.end_date)
    regime_inputs = compute_regime_inputs(settings, req.start_date, req.end_date, benchmark_df)

    entry_gate, entry_size_mult = {}, {}
    if settings.backtest.use_regime_entry_controls:
        entry_gate, entry_size_mult = build_entry_controls(settings, regime_inputs)

    # Run Simulation
    sim_result = simulate_portfolio_python(
        signals_df,
        ohlcv_df,
        params,
        portfolio_cfg,
        sector_map,
        exit_config=_settings_object_dict(settings.exit_strategy),
        entry_gate=entry_gate,
        entry_size_mult=entry_size_mult,
    )

    # The canonical engine returns `trades` as a polars frame that already
    # includes still-open positions (status "open") and expired triggers
    # ("not_filled"); the retired fork returned dataclasses plus separate
    # open_trades / pending_trades snapshots. Convert once here so the summary,
    # post-entry analysis, and serialisation below are unchanged.
    trades = _trades_from_frame(sim_result["trades"])
    equity_curve = sim_result["daily_equity"]

    signal_analysis_raw = analyze_signal_quality(
        signals_df, ohlcv_df, pending_days=pending_days
    )
    post_entry = analyze_post_entry_returns(trades, ohlcv_df, forward_days=10)
    signal_analysis_raw["post_entry_returns"] = post_entry

    summary = summarize_backtest(trades, exit_mode=params.exit_mode)

    # Open positions ride along in `trades` with status "open"; summarize_backtest
    # filters to closed trades, so summary metrics stay closed-trade only.
    trades_json = [_serialize_trade(t) for t in trades]
    equity_json = []
    for row in equity_curve.to_dicts():
        # Convert date and map to UI cumulativeR field (using equity as proxy)
        row["exit_date"] = row["date"].isoformat()
        row["cumulative_r"] = (row["equity"] / float(portfolio_cfg["initial_capital"])) - 1.0
        equity_json.append(row)

    summary_dict = asdict(summary)
    # Add terminal wealth info to summary
    summary_dict["terminal_wealth"] = float(equity_curve["equity"][-1])
    summary_dict["total_return_pct"] = (summary_dict["terminal_wealth"] / float(portfolio_cfg["initial_capital"]) - 1.0) * 100

    response = {
        "params": {
            "start_date": req.start_date.isoformat(),
            "end_date": req.end_date.isoformat(),
            "exit_mode": exit_mode.value,
            "target_r": req.target_r,
            "pending_days": pending_days,
            "max_hold_days": max_hold_days,
            "breakeven_activation_r": settings.exit_strategy.breakeven_activation_r,
            "partial_profit_r": settings.exit_strategy.partial_profit_r,
            "trail_pct_bull": settings.exit_strategy.trail_pct_bull,
            "trail_pct_bear": settings.exit_strategy.trail_pct_bear,
            "time_stop_days": settings.exit_strategy.time_stop_days,
            "exit_engine": (
                "minervini_phased"
                if exit_mode == ExitMode.MINERVINI_PHASED
                else req.exit_engine
            ),
        },
        "summary": summary_dict,
        "trades": trades_json,
        "equity_curve": equity_json,
        "signal_analysis": signal_analysis_raw,
    }

    cache = _cache_path(settings)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(response, default=str), encoding="utf-8")

    return response


@router.get("/last")
def get_last_backtest(
    settings: AppSettings = Depends(get_settings),
) -> dict:
    cache = _cache_path(settings)
    if not cache.exists():
        raise HTTPException(status_code=404, detail="No cached backtest result")
    return json.loads(cache.read_text(encoding="utf-8"))


class ScorecardRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    forward_days: list[int] = [1, 3, 5, 10, 20]


@router.post("/signal-scorecard")
def signal_scorecard(
    req: ScorecardRequest,
    settings: AppSettings = Depends(get_settings),
    store: DuckDbStore = Depends(get_duckdb_store),
    parquet: ParquetOhlcvStore = Depends(get_parquet_store),
) -> dict:
    store.initialize()
    conn = store._connect()
    signals_df = get_backtest_signals(conn, req.start_date, req.end_date)
    if signals_df.is_empty():
        raise HTTPException(status_code=404, detail="No breakout signals in date range")

    # Get diagnostics for contraction data
    diag_query = "SELECT * FROM daily_diagnostics WHERE 1=1"
    diag_params: list[object] = []
    if req.start_date:
        diag_query += " AND diag_date >= ?"
        diag_params.append(req.start_date)
    if req.end_date:
        diag_query += " AND diag_date <= ?"
        diag_params.append(req.end_date)
    diagnostics = conn.execute(diag_query, diag_params).pl()

    symbols = signals_df["symbol"].unique().to_list()
    max_fwd = max(req.forward_days)
    latest_signal = signals_df["signal_date"].max()
    extended_end = latest_signal + timedelta(days=int(max_fwd * 1.5))
    earliest_signal = signals_df["signal_date"].min()
    ohlcv_df = get_backtest_ohlcv(parquet, symbols, earliest_signal, extended_end)

    return compute_scorecard(
        signals_df,
        ohlcv_df,
        diagnostics,
        req.forward_days,
        pending_days=settings.backtest.pending_days,
    )
