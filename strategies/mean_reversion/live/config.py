from __future__ import annotations

import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data" / "live"
LIVE_STATE_PATH = DATA_DIR / "mr_portfolio.json"
PAPER_STATE_PATH = DATA_DIR / "mr_paper_portfolio.json"
PAPER_TRADES_PATH = DATA_DIR / "mr_paper_trades.csv"
OHLCV_PATH = ROOT / "data" / "yf_adjusted" / "ohlcv_all.parquet"
SECTOR_MAP_PATH = ROOT / "data" / "reference" / "nifty500_constituents.csv"


def _initial_capital() -> float:
    raw = os.getenv("MR_CAPITAL")
    if raw is None or raw.strip() == "":
        return 500_000.0
    return float(raw)


LOCKED_PARAMS: dict[str, Any] = {
    "rsi_threshold": 35.0,
    "stop_atr_mult": 3.5,
    "exit_mode": "sma3",
    "time_stop_days": 5,
    "max_hold_days": 20,
    "risk_per_trade_pct": 0.04,
    "max_positions": 5,
    "max_sector_positions": 3,
    "round_trip_cost_pct": 0.002,
    "initial_capital": _initial_capital(),
    "min_adv_inr": 100_000_000.0,
    "min_price": 50.0,
    "sma_trend_period": 200,
}

