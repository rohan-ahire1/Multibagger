from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sepa_scanner.api.deps import get_duckdb_store, get_parquet_store, get_settings
from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.metrics import build_equity_curve, summarize_backtest
from sepa_scanner.backtest.models import BacktestParams, ExitMode
from sepa_scanner.backtest.queries import get_backtest_ohlcv, get_backtest_signals
from sepa_scanner.settings import AppSettings
from sepa_scanner.storage.duckdb_store import DuckDbStore
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore

router = APIRouter(prefix="/backtest", tags=["backtest"])


class BacktestRunRequest(BaseModel):
    start_date: date
    end_date: date
    exit_mode: str = "v2_trail_10d"
    target_r: float | None = None
    pending_days: int = 5
    max_hold_days: int = 90
    trail_activation_r: float = 2.0
    trail_pct: float = 0.20
    dead_money_days: int | None = 15
    exit_engine: str = "legacy"


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


@router.post("/run")
def run_backtest(
    req: BacktestRunRequest,
    settings: AppSettings = Depends(get_settings),
    store: DuckDbStore = Depends(get_duckdb_store),
    parquet: ParquetOhlcvStore = Depends(get_parquet_store),
) -> dict:
    if req.exit_engine == "minervini_phased":
        exit_mode = ExitMode.MINERVINI_PHASED
    else:
        exit_mode = ExitMode(req.exit_mode)

    params = BacktestParams(
        pending_days=req.pending_days,
        max_hold_days=req.max_hold_days,
        target_r=req.target_r,
        exit_mode=exit_mode,
        trail_activation_r=req.trail_activation_r,
        trail_pct=req.trail_pct,
        dead_money_days=req.dead_money_days,
    )
    store.initialize()
    conn = store._connect()
    signals_df = get_backtest_signals(conn, req.start_date, req.end_date)
    if signals_df.is_empty():
        raise HTTPException(status_code=404, detail="No breakout signals in date range")

    symbols = signals_df["symbol"].unique().to_list()
    extended_end = req.end_date + timedelta(days=int(req.max_hold_days * 1.5))
    ohlcv_df = get_backtest_ohlcv(parquet, symbols, req.start_date, extended_end)

    result = simulate_breakout_backtest(signals_df, ohlcv_df, params)
    summary = summarize_backtest(result.trades, exit_mode=params.exit_mode)
    equity_curve = build_equity_curve(result.trades)

    trades_json = [_serialize_trade(t) for t in result.trades]
    equity_json = []
    for row in equity_curve.to_dicts():
        row["exit_date"] = row["exit_date"].isoformat() if row.get("exit_date") else None
        equity_json.append(row)

    summary_dict = asdict(summary)
    response = {
        "params": {
            "start_date": req.start_date.isoformat(),
            "end_date": req.end_date.isoformat(),
            "exit_mode": req.exit_mode,
            "target_r": req.target_r,
            "pending_days": req.pending_days,
            "max_hold_days": req.max_hold_days,
            "trail_activation_r": req.trail_activation_r,
            "trail_pct": req.trail_pct,
            "dead_money_days": req.dead_money_days,
            "exit_engine": req.exit_engine,
        },
        "summary": summary_dict,
        "trades": trades_json,
        "equity_curve": equity_json,
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
