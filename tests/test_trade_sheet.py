"""Tests for the Phase-1 daily trade sheet sizing (ENTER orders)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import importlib.util

_spec = importlib.util.spec_from_file_location("sepa_paper_daily", _ROOT / "scripts" / "sepa_paper_daily.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build_trade_sheet = _mod.build_trade_sheet


def _sheet(**kw):
    base = dict(
        as_of="2026-05-29", latest_date=date(2026, 5, 29),
        holdings=[], today_exits=pl.DataFrame(),
        equity=1_000_000.0, cash=1_000_000.0,
        sector_map={"ABC": "Tech"}, risk_pct=0.02, max_name_pct=0.20, max_positions=20,
    )
    base.update(kw)
    return build_trade_sheet(**base)


def test_enter_sizing_risk_bound():
    # equity 1M, risk 2% = 20k budget; entry 100 stop 95 -> risk 5/sh -> 4000 sh by risk,
    # but per-name cap 20% of 1M / 100 = 2000 sh binds.
    signals = pl.DataFrame({
        "signal_date": [date(2026, 5, 29)], "symbol": ["ABC"],
        "entry_ref": [100.0], "stop": [95.0], "rs_rank": [85.0],
    })
    out = _sheet(signals=signals)
    assert "ABC" in out
    assert "2000" in out  # per-name cap binds


def test_enter_sizing_risk_binds_when_cap_loose():
    # wide stop -> risk budget binds below the per-name cap
    signals = pl.DataFrame({
        "signal_date": [date(2026, 5, 29)], "symbol": ["ABC"],
        "entry_ref": [100.0], "stop": [50.0], "rs_rank": [85.0],  # risk 50/sh -> 400 sh
    })
    out = _sheet(signals=signals)
    assert "400" in out


def test_no_breakouts_today():
    signals = pl.DataFrame({
        "signal_date": [date(2026, 5, 1)], "symbol": ["ABC"],  # not today
        "entry_ref": [100.0], "stop": [95.0], "rs_rank": [85.0],
    })
    out = _sheet(signals=signals)
    assert "no new breakouts today" in out


def test_exit_action_mapping():
    exits = pl.DataFrame({"symbol": ["XYZ"], "status": ["partial_profit"], "exit_price": [120.0]})
    out = _sheet(signals=pl.DataFrame(schema={"signal_date": pl.Date, "symbol": pl.Utf8}), today_exits=exits)
    assert "SELL HALF" in out and "XYZ" in out
