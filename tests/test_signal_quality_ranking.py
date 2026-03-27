from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.portfolio_diagnostics import filter_signals
from sepa_scanner.backtest.signal_scorecard import compute_scorecard


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
        diagnostics=diagnostics,
        ranking_mode="offense_v2",
        quality_overrides={"offense_score_min": 7.0},
    )

    assert ranked["symbol"].to_list() == ["ALPHA"]


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
            "entry_ref": [100.0],
            "stop": [95.0],
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
            "high": [101.0, 106.0, 112.0, 116.0, 117.0, 118.0],
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

    result = compute_scorecard(signals, ohlcv, diagnostics, forward_days=[1, 3, 5])

    assert result["overall"]["reached_2r_pct"] == 100.0
    assert result["overall"]["reached_3r_pct"] == 100.0
    assert "by_setup_streak" in result
    assert "by_offense_v2_score" in result
