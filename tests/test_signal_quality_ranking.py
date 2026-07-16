from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
from types import SimpleNamespace

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.portfolio_diagnostics import filter_signals, simulate_portfolio_python
from sepa_scanner.backtest.models import BacktestParams, ExitMode
from sepa_scanner.backtest.signal_scorecard import compute_scorecard
from sepa_scanner.strategy.ranking import add_signal_score, add_signal_size_multiplier


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        backtest=SimpleNamespace(
            sector_alpha_enabled=False,
            sector_alpha_threshold_high=85.0,
            sector_alpha_threshold_low=60.0,
            sector_alpha_mult_high=1.2,
            sector_alpha_mult_low=0.8,
        )
    )


def test_offense_v2_ranking_prefers_richer_setup_quality() -> None:
    signals = pl.DataFrame(
        {
            "signal_date": [date(2026, 3, 1), date(2026, 3, 1)],
            "symbol": ["ALPHA", "BETA"],
            "signal_type": ["BREAKOUT", "BREAKOUT"],
            "close": [100.0, 100.0],
            "pivot": [100.0, 100.0],
            "near_pivot": [0.005, 0.005],
            "volume": [1_000_000, 1_000_000],
            "vol50": [400_000.0, 400_000.0],
            "vol_mult": [2.2, 2.2],
            "rs_rank": [90.0, 92.0],
            "range10": [0.06, 0.06],
            "voldry": [0.40, 0.40],
            "atr_pct": [0.03, 0.03],
            "adv50": [1_000_000_000.0, 1_000_000_000.0],
            "entry_ref": [100.0, 100.0],
            "stop": [94.0, 94.0],
            "r_per_share": [6.0, 6.0],
            "created_at": [None, None],
        }
    )
    diagnostics = pl.DataFrame(
        {
            "diag_date": [date(2026, 3, 1), date(2026, 3, 1)],
            "symbol": ["ALPHA", "BETA"],
            "tt_pass": [True, True],
            "rs_pass": [True, True],
            "setup_pass": [True, True],
            "vcp1_range10_ok": [True, True],
            "vcp2_voldry_ok": [True, True],
            "vcp3_atr_trend_ok": [True, True],
            "vcp4_atr_contract_ok": [True, False],
            "vcp5_extension_ok": [True, False],
            "vcp6_base_depth_ok": [True, True],
            "contraction_count": [3, 1],
            "contraction_quality": [0.85, 0.25],
            "setup_active_streak": [6, 1],
            "pivot_age_days": [1, 10],
            "watch_fail_mask": [0, 0],
            "breakout_fail_mask": [0, 16],
        }
    )

    ranked = filter_signals(
        signals,
        settings=_settings(),
        diagnostics=diagnostics,
        ranking_mode="offense_v2",
        max_signals_per_day=1,
    )

    assert ranked.height == 1
    assert ranked["symbol"].to_list() == ["ALPHA"]
    assert set(ranked.columns) == set(signals.columns)


def test_offense_v2_score_threshold_filters_using_diagnostics() -> None:
    signals = pl.DataFrame(
        {
            "signal_date": [date(2026, 3, 1), date(2026, 3, 1)],
            "symbol": ["ALPHA", "BETA"],
            "signal_type": ["BREAKOUT", "BREAKOUT"],
            "close": [100.0, 100.0],
            "pivot": [100.0, 100.0],
            "near_pivot": [0.005, 0.025],
            "volume": [1_000_000, 1_000_000],
            "vol50": [400_000.0, 400_000.0],
            "vol_mult": [2.2, 1.4],
            "rs_rank": [90.0, 86.0],
            "range10": [0.06, 0.09],
            "voldry": [0.40, 0.60],
            "atr_pct": [0.03, 0.05],
            "adv50": [1_000_000_000.0, 500_000_000.0],
            "entry_ref": [100.0, 100.0],
            "stop": [94.0, 94.0],
            "r_per_share": [6.0, 6.0],
            "created_at": [None, None],
        }
    )
    diagnostics = pl.DataFrame(
        {
            "diag_date": [date(2026, 3, 1), date(2026, 3, 1)],
            "symbol": ["ALPHA", "BETA"],
            "tt_pass": [True, True],
            "rs_pass": [True, True],
            "setup_pass": [True, True],
            "vcp1_range10_ok": [True, False],
            "vcp2_voldry_ok": [True, False],
            "vcp3_atr_trend_ok": [True, True],
            "vcp4_atr_contract_ok": [True, False],
            "vcp5_extension_ok": [True, False],
            "vcp6_base_depth_ok": [True, True],
            "contraction_count": [3, 1],
            "contraction_quality": [0.85, 0.25],
            "setup_active_streak": [6, 1],
            "pivot_age_days": [1, 10],
            "watch_fail_mask": [0, 0],
            "breakout_fail_mask": [0, 16],
        }
    )

    ranked = filter_signals(
        signals,
        settings=_settings(),
        diagnostics=diagnostics,
        ranking_mode="offense_v2",
        quality_overrides={"offense_score_min": 7.0},
    )

    assert ranked["symbol"].to_list() == ["ALPHA"]


def test_offense_v2_size_tilt_assigns_larger_multiplier_to_better_setups() -> None:
    signals = pl.DataFrame(
        {
            "signal_date": [date(2026, 3, 1), date(2026, 3, 1)],
            "symbol": ["ALPHA", "BETA"],
            "signal_type": ["BREAKOUT", "BREAKOUT"],
            "close": [100.0, 100.0],
            "pivot": [100.0, 100.0],
            "near_pivot": [0.005, 0.025],
            "volume": [1_000_000, 1_000_000],
            "vol50": [400_000.0, 400_000.0],
            "vol_mult": [2.3, 1.3],
            "rs_rank": [91.0, 84.0],
            "range10": [0.06, 0.10],
            "voldry": [0.40, 0.62],
            "atr_pct": [0.03, 0.05],
            "adv50": [1_000_000_000.0, 500_000_000.0],
            "entry_ref": [100.0, 100.0],
            "stop": [95.0, 95.0],
            "r_per_share": [5.0, 5.0],
            "created_at": [None, None],
        }
    )
    diagnostics = pl.DataFrame(
        {
            "diag_date": [date(2026, 3, 1), date(2026, 3, 1)],
            "symbol": ["ALPHA", "BETA"],
            "tt_pass": [True, True],
            "rs_pass": [True, True],
            "setup_pass": [True, True],
            "vcp1_range10_ok": [True, False],
            "vcp2_voldry_ok": [True, False],
            "vcp3_atr_trend_ok": [True, True],
            "vcp4_atr_contract_ok": [True, False],
            "vcp5_extension_ok": [True, False],
            "vcp6_base_depth_ok": [True, True],
            "contraction_count": [2, 1],
            "contraction_quality": [0.85, 0.20],
            "setup_active_streak": [6, 1],
            "pivot_age_days": [1, 10],
            "watch_fail_mask": [0, 4],
            "breakout_fail_mask": [0, 16],
        }
    )

    ranked = filter_signals(
        signals,
        settings=_settings(),
        diagnostics=diagnostics,
        ranking_mode="offense_v2",
        quality_overrides={
            "size_tilt_mode": "offense_v2",
            "size_tilt_score_min": 5.5,
            "size_tilt_score_max": 8.0,
            "size_tilt_min_mult": 0.6,
            "size_tilt_max_mult": 1.4,
        },
    )

    assert "signal_size_mult" in ranked.columns
    mults = {row["symbol"]: row["signal_size_mult"] for row in ranked.to_dicts()}
    assert mults["ALPHA"] > mults["BETA"]
    assert 0.6 <= mults["BETA"] <= 1.4
    assert 0.6 <= mults["ALPHA"] <= 1.4


def test_simulate_portfolio_python_uses_signal_size_multiplier_for_position_sizing() -> None:
    signals = pl.DataFrame(
        {
            "signal_date": [date(2026, 3, 1), date(2026, 3, 1)],
            "symbol": ["ALPHA", "BETA"],
            "entry_ref": [100.0, 100.0],
            "stop": [95.0, 95.0],
            "pivot": [100.0, 100.0],
            "rs_rank": [90.0, 90.0],
            "signal_size_mult": [1.4, 0.6],
        }
    )
    ohlcv = pl.DataFrame(
        {
            "date": [
                date(2026, 3, 1),
                date(2026, 3, 2),
                date(2026, 3, 1),
                date(2026, 3, 2),
            ],
            "symbol": ["ALPHA", "ALPHA", "BETA", "BETA"],
            "open": [100.0, 100.0, 100.0, 100.0],
            "high": [101.0, 102.0, 101.0, 102.0],
            "low": [99.0, 99.0, 99.0, 99.0],
            "close": [100.0, 101.0, 100.0, 101.0],
            "volume": [1_000_000, 1_000_000, 1_000_000, 1_000_000],
        }
    )
    params = BacktestParams(
        pending_days=1,
        max_hold_days=1,
        target_r=None,
        exit_mode=ExitMode("v3_trail_pct"),
        trail_activation_r=10.0,
        trail_pct=0.25,
        dead_money_days=None,
    )
    portfolio = {
        "initial_capital": 100_000.0,
        "risk_per_trade_pct": 0.02,
        "max_positions": 5,
        "max_sector_positions": 0,
        "max_sector_pct": 1.0,
        "max_heat_pct": 1.0,
        "drawdown_half_pct": 0.50,
        "drawdown_stop_pct": 0.75,
        "drawdown_close_pct": 0.90,
    }

    result = simulate_portfolio_python(
        signals,
        ohlcv,
        params,
        portfolio,
        {"ALPHA": "TECH", "BETA": "TECH"},
    )

    trades = {
        row["symbol"]: row
        for row in result["trades"].filter(pl.col("entry_date").is_not_null()).to_dicts()
    }
    assert trades["ALPHA"]["shares"] > trades["BETA"]["shares"]
    assert round(trades["ALPHA"]["shares"] / trades["BETA"]["shares"], 3) == round(1.4 / 0.6, 3)


def test_offense_v2_band_tilt_peaks_near_midpoint_score() -> None:
    frame = pl.DataFrame(
        {
            "signal_date": [date(2026, 3, 1)] * 3,
            "symbol": ["ALPHA", "BETA", "GAMMA"],
            "rs_rank": [90.0, 82.0, 95.0],
            "vol_mult": [2.0, 1.1, 2.8],
            "range10": [0.065, 0.11, 0.05],
            "voldry": [0.40, 0.70, 0.28],
            "near_pivot": [0.01, 0.05, 0.002],
            "adv50": [1_000_000_000.0, 400_000_000.0, 1_500_000_000.0],
            "atr_pct": [0.03, 0.06, 0.025],
            "entry_ref": [100.0, 100.0, 100.0],
            "r_per_share": [5.5, 7.0, 4.5],
            "contraction_quality": [0.78, 0.25, 0.90],
            "contraction_count": [2.0, 1.0, 3.0],
            "setup_active_streak": [5.0, 1.0, 7.0],
            "pivot_age_days": [2.0, 12.0, 0.0],
            "tt_pass": [1.0, 1.0, 1.0],
            "setup_pass": [1.0, 1.0, 1.0],
            "rs_pass": [1.0, 1.0, 1.0],
            "vcp1_range10_ok": [1.0, 0.0, 1.0],
            "vcp2_voldry_ok": [1.0, 0.0, 1.0],
            "vcp4_atr_contract_ok": [1.0, 0.0, 1.0],
            "vcp5_extension_ok": [1.0, 0.0, 1.0],
            "vcp6_base_depth_ok": [1.0, 1.0, 1.0],
            "watch_fail_mask": [0.0, 4.0, 0.0],
            "breakout_fail_mask": [0.0, 16.0, 0.0],
        }
    )

    scored = add_signal_score(frame, ranking_mode="offense_v2", score_column="score")
    linear = add_signal_size_multiplier(
        frame,
        tilt_mode="offense_v2",
        score_min=7.0,
        score_max=8.0,
        min_mult=0.7,
        max_mult=1.3,
    )
    banded = add_signal_size_multiplier(
        frame,
        tilt_mode="offense_v2_band",
        score_min=7.0,
        score_max=8.0,
        min_mult=0.7,
        max_mult=1.3,
    )
    merged = (
        scored.join(linear.select("symbol", pl.col("signal_size_mult").alias("linear_mult")), on="symbol", how="left")
        .join(banded.select("symbol", pl.col("signal_size_mult").alias("band_mult")), on="symbol", how="left")
    )
    highest = merged.sort("score", descending=True).head(1)

    assert highest["score"].item() > 8.0
    assert highest["band_mult"].item() < highest["linear_mult"].item()


def test_scorecard_reports_multi_r_outcomes_and_quality_sections() -> None:
    signals = pl.DataFrame(
        {
            "signal_date": [date(2026, 3, 1)],
            "symbol": ["ALPHA"],
            "signal_type": ["BREAKOUT"],
            "rs_rank": [91.0],
            "vol_mult": [2.5],
            "range10": [0.05],
            "voldry": [0.38],
            "atr_pct": [0.028],
            "entry_ref": [105.0],
            "stop": [100.0],
            "r_per_share": [5.0],
        }
    )
    ohlcv = pl.DataFrame(
        {
            "date": [
                date(2026, 3, 1),
                date(2026, 3, 2),
                date(2026, 3, 3),
                date(2026, 3, 4),
                date(2026, 3, 5),
                date(2026, 3, 6),
            ],
            "symbol": ["ALPHA"] * 6,
            "open": [100.0, 101.0, 104.0, 109.0, 112.0, 114.0],
            "high": [101.0, 106.0, 112.0, 120.0, 121.0, 122.0],
            "low": [99.0, 100.0, 103.0, 108.0, 111.0, 113.0],
            "close": [100.0, 105.0, 110.0, 114.0, 116.0, 117.0],
            "volume": [1_000_000] * 6,
        }
    )
    diagnostics = pl.DataFrame(
        {
            "diag_date": [date(2026, 3, 1)],
            "symbol": ["ALPHA"],
            "tt_pass": [True],
            "rs_pass": [True],
            "setup_pass": [True],
            "vcp1_range10_ok": [True],
            "vcp2_voldry_ok": [True],
            "vcp3_atr_trend_ok": [True],
            "vcp4_atr_contract_ok": [True],
            "vcp5_extension_ok": [True],
            "vcp6_base_depth_ok": [True],
            "contraction_count": [3],
            "contraction_quality": [0.82],
            "near_pivot": [0.005],
            "setup_active_streak": [5],
            "pivot_age_days": [2],
            "watch_fail_mask": [0],
            "breakout_fail_mask": [0],
        }
    )

    result = compute_scorecard(
        signals,
        ohlcv,
        diagnostics,
        forward_days=[1, 3, 5],
        pending_days=3,
    )

    assert result["overall"]["fill_rate_pct"] == 100.0
    assert result["overall"]["reached_2r_pct"] == 100.0
    assert result["overall"]["reached_3r_pct"] == 100.0
    assert "by_setup_streak" in result
    assert "by_offense_v2_score" in result
