from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from sepa_scanner.settings import (
    AlertsSettings,
    AppSettings,
    BreakoutSettings,
    DataSettings,
    MarketFilterSettings,
    MarketSettings,
    RelativeStrengthSettings,
    RiskSettings,
    RuntimeSettings,
    SetupVcpSettings,
    TelegramSettings,
    TrendTemplateSettings,
    UniverseSettings,
)
from sepa_scanner.strategy.signal_engine import scan_for_date


def _dates(count: int) -> list[date]:
    start = date(2026, 1, 1)
    return [start + timedelta(days=offset) for offset in range(count)]


def _symbol_frame(
    symbol: str, dates: list[date], closes: list[float], volumes: list[int]
) -> pl.DataFrame:
    highs = [close + 0.3 for close in closes]
    lows = [close - 0.3 for close in closes]
    return pl.DataFrame(
        {
            "date": dates,
            "symbol": [symbol] * len(dates),
            "series": ["EQ"] * len(dates),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "turnover": [close * volume for close, volume in zip(closes, volumes, strict=True)],
        }
    )


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        base_dir=tmp_path,
        config_dir=tmp_path / "config",
        market=MarketSettings(
            timezone="Asia/Kolkata",
            benchmark_symbol="NIFTYBEES",
            scan_time_ist="16:10",
        ),
        data=DataSettings(
            redownload_days=5,
            min_history_days=50,
            bhavcopy_api_key="favCapital",
            http_timeout_sec=30,
            max_retries=5,
            apply_corporate_actions_adjustment=False,
        ),
        universe=UniverseSettings(
            allowed_series=["EQ"],
            min_price_inr=50.0,
            min_adv50_inr=10_000_000.0,
            min_avg_volume_shares=100_000,
            max_symbols=10,
        ),
        trend_template=TrendTemplateSettings(
            sma_fast=5,
            sma_mid=8,
            sma_slow=10,
            lookback_52w=20,
            sma200_slope_lookback=3,
            min_above_52w_low_pct=0.05,
            max_below_52w_high_pct=0.10,
        ),
        relative_strength=RelativeStrengthSettings(
            lookbacks=[5, 10, 15, 20],
            weights=[0.4, 0.2, 0.2, 0.2],
            rs_rank_min=50,
        ),
        setup_vcp=SetupVcpSettings(
            base_lookback=5,
            max_range10_pct=0.08,
            max_vol_dryup_ratio=0.80,
            atr_period=14,
            atr_contraction_factor=0.95,
            max_extension_from_50sma_pct=0.10,
            base_max_depth_pct=0.30,
            pre_alert_proximity_pct=0.03,
            contraction_min_count=0,
        ),
        breakout=BreakoutSettings(
            require_market_filter=True,
            breakout_buffer_pct=0.001,
            breakout_vol_mult=1.5,
        ),
        risk=RiskSettings(
            entry_buffer_pct=0.001,
            max_stop_pct=0.08,
        ),
        alerts=AlertsSettings(
            max_alerts_per_day=10,
            send_watch_alerts=True,
            send_breakout_alerts=True,
            channels=["console"],
        ),
        market_filter=MarketFilterSettings(),
        telegram=TelegramSettings(),
        runtime=RuntimeSettings(data_dir=tmp_path / "data", polars_max_threads=4),
        alert_channels={},
        manual_universe=[],
    )


def test_signal_engine_emits_watch_and_breakout_with_prev_day_setup(tmp_path: Path) -> None:
    dates = _dates(61)
    benchmark = _symbol_frame(
        "NIFTYBEES",
        dates,
        [100.0 + 0.5 * idx for idx in range(61)],
        [1_000_000] * 61,
    )
    watch = _symbol_frame(
        "WATCHME",
        dates,
        [70.0 + idx for idx in range(50)]
        + [119.2, 119.5, 119.8, 120.0, 120.1, 120.2, 120.25, 120.3, 120.35, 120.4, 120.45],
        [450_000] * 50 + [180_000] * 11,
    )
    breakout = _symbol_frame(
        "BREAKME",
        dates,
        [75.0 + 0.9 * idx for idx in range(50)]
        + [119.2, 119.4, 119.5, 119.6, 119.7, 119.8, 119.85, 119.9, 119.95, 121.5, 122.0],
        [350_000] * 50 + [150_000] * 9 + [5_000_000, 1_500_000],
    )
    weak = _symbol_frame(
        "WEAK",
        dates,
        [90.0 + 0.1 * idx for idx in range(61)],
        [300_000] * 61,
    )

    frame = pl.concat([benchmark, watch, breakout, weak], how="vertical")
    settings = _settings(tmp_path)

    day60 = scan_for_date(frame=frame, settings=settings, scan_date=dates[59])
    day61 = scan_for_date(frame=frame, settings=settings, scan_date=dates[60])

    assert day60.market_filter_on is True
    assert set(day60.signals["symbol"]) == {"WATCHME", "BREAKME"}
    assert set(day60.signals["signal_type"]) == {"WATCH", "BREAKOUT"}
    assert "NIFTYBEES" not in day60.signals["symbol"].to_list()

    breakout_row = day60.signals.filter(pl.col("symbol") == "BREAKME").to_dicts()[0]
    assert breakout_row["signal_type"] == "BREAKOUT"
    assert breakout_row["voldry"] < settings.setup_vcp.max_vol_dryup_ratio

    assert day61.signals.filter(pl.col("symbol") == "BREAKME").is_empty()


def test_watch_rejects_above_pivot(tmp_path: Path) -> None:
    """WATCH requires near_pivot >= 0 (close at or below pivot)."""
    dates = _dates(61)
    benchmark = _symbol_frame(
        "NIFTYBEES",
        dates,
        [100.0 + 0.5 * idx for idx in range(61)],
        [1_000_000] * 61,
    )
    above_pivot = _symbol_frame(
        "ABOVE",
        dates,
        [70.0 + idx for idx in range(50)]
        + [119.2, 119.5, 119.8, 120.0, 120.1, 120.2, 120.25, 120.3, 120.35, 120.4, 121.5],
        [450_000] * 50 + [180_000] * 11,
    )
    weak = _symbol_frame(
        "WEAK",
        dates,
        [90.0 + 0.1 * idx for idx in range(61)],
        [300_000] * 61,
    )

    frame = pl.concat([benchmark, above_pivot, weak], how="vertical")
    settings = _settings(tmp_path)
    result = scan_for_date(frame=frame, settings=settings, scan_date=dates[60])

    above_watch = result.signals.filter(
        (pl.col("symbol") == "ABOVE") & (pl.col("signal_type") == "WATCH")
    )
    assert above_watch.is_empty(), "Stock with close above pivot should not produce WATCH"


def test_breakout_stop_uses_prev_day_structural_stop(tmp_path: Path) -> None:
    """BREAKOUT stop is based on D-1 structural stop, excluding breakout day."""
    dates = _dates(61)
    benchmark = _symbol_frame(
        "NIFTYBEES",
        dates,
        [100.0 + 0.5 * idx for idx in range(61)],
        [1_000_000] * 61,
    )
    closes = (
        [75.0 + 0.9 * idx for idx in range(50)]
        + [119.2, 119.4, 119.5, 119.6, 119.7, 119.8, 119.85, 119.9, 119.95, 121.5, 122.0]
    )
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.3 for c in closes]
    lows[59] = 100.0  # artificially low on breakout day
    breakout_df = pl.DataFrame(
        {
            "date": dates,
            "symbol": ["BREAKME"] * len(dates),
            "series": ["EQ"] * len(dates),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [350_000] * 50 + [150_000] * 9 + [5_000_000, 1_500_000],
            "turnover": [c * v for c, v in zip(closes, [350_000] * 50 + [150_000] * 9 + [5_000_000, 1_500_000], strict=True)],
        }
    )
    weak = _symbol_frame(
        "WEAK",
        dates,
        [90.0 + 0.1 * idx for idx in range(61)],
        [300_000] * 61,
    )

    frame = pl.concat([benchmark, breakout_df, weak], how="vertical")
    settings = _settings(tmp_path)
    result = scan_for_date(frame=frame, settings=settings, scan_date=dates[59])

    bo_rows = result.signals.filter(
        (pl.col("symbol") == "BREAKME") & (pl.col("signal_type") == "BREAKOUT")
    )
    assert bo_rows.height == 1, "Expected a BREAKOUT signal"
    stop = bo_rows["stop"][0]
    assert stop > 100.0, (
        f"Stop {stop} should be based on D-1 struct_stop (not including day D low of 100.0)"
    )


def test_breakout_rejected_when_overextended(tmp_path: Path) -> None:
    """BREAKOUT rejected when close > sma50 * (1 + max_extension_breakout_pct)."""
    dates = _dates(61)
    benchmark = _symbol_frame(
        "NIFTYBEES",
        dates,
        [100.0 + 0.5 * idx for idx in range(61)],
        [1_000_000] * 61,
    )
    closes = (
        [75.0 + 0.9 * idx for idx in range(50)]
        + [119.2, 119.4, 119.5, 119.6, 119.7, 119.8, 119.85, 119.9, 119.95, 145.0, 146.0]
    )
    extended = _symbol_frame(
        "EXTENDED",
        dates,
        closes,
        [350_000] * 50 + [150_000] * 9 + [5_000_000, 1_500_000],
    )
    weak = _symbol_frame(
        "WEAK",
        dates,
        [90.0 + 0.1 * idx for idx in range(61)],
        [300_000] * 61,
    )

    frame = pl.concat([benchmark, extended, weak], how="vertical")
    settings = _settings(tmp_path)
    result = scan_for_date(frame=frame, settings=settings, scan_date=dates[59])

    bo = result.signals.filter(
        (pl.col("symbol") == "EXTENDED") & (pl.col("signal_type") == "BREAKOUT")
    )
    assert bo.is_empty(), "Overextended stock (close >> sma50) should not produce BREAKOUT"


def test_signal_engine_returns_ranked_watchlist_candidates(tmp_path: Path) -> None:
    dates = _dates(61)
    benchmark = _symbol_frame(
        "NIFTYBEES",
        dates,
        [100.0 + 0.5 * idx for idx in range(61)],
        [1_000_000] * 61,
    )
    watch = _symbol_frame(
        "WATCHME",
        dates,
        [70.0 + idx for idx in range(50)]
        + [119.2, 119.5, 119.8, 120.0, 120.1, 120.2, 120.25, 120.3, 120.35, 120.4, 120.45],
        [450_000] * 50 + [180_000] * 11,
    )
    candidate = _symbol_frame(
        "CANDY",
        dates,
        [60.0 + idx for idx in range(50)]
        + [116.0, 116.5, 117.0, 117.4, 117.8, 118.0, 118.1, 118.15, 118.2, 118.25, 118.3],
        [420_000] * 50 + [170_000] * 11,
    )
    weak = _symbol_frame(
        "WEAK",
        dates,
        [90.0 + 0.1 * idx for idx in range(61)],
        [300_000] * 61,
    )

    frame = pl.concat([benchmark, watch, candidate, weak], how="vertical")
    settings = _settings(tmp_path)

    result = scan_for_date(frame=frame, settings=settings, scan_date=dates[59])

    assert result.watchlist_candidates.height >= 1
    assert "WATCHME" in result.watchlist_candidates["symbol"].to_list()
    assert "NIFTYBEES" not in result.watchlist_candidates["symbol"].to_list()
    assert result.watchlist_candidates.columns == [
        "watch_date",
        "symbol",
        "close",
        "pivot",
        "near_pivot",
        "rs_rank",
        "range10",
        "voldry",
        "atr_pct",
        "adv50",
        "vol50",
    ]


def test_prepare_frame_uses_adjusted_prices_when_corp_actions_provided(tmp_path: Path) -> None:
    """When corp_actions is passed, _prepare_frame should adjust prices before computing indicators."""
    from sepa_scanner.strategy.signal_engine import _prepare_frame

    dates_list = [date(2020, 1, 1) + timedelta(days=i) for i in range(320)]
    # Split at day 315: pre-split close=200, post-split close=100 (2:1 split, factor=0.5)
    abc_close = [200.0] * 315 + [100.0] * 5
    abc_rows = []
    for i, d in enumerate(dates_list):
        c = abc_close[i]
        abc_rows.append({
            "date": d, "symbol": "ABC", "series": "EQ",
            "open": c, "high": c * 1.01, "low": c * 0.99,
            "close": c, "volume": 100000, "turnover": c * 100000,
        })

    frame = pl.DataFrame(abc_rows)
    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "ex_date": [dates_list[315]],
        "adjustment_factor": [0.5],
    })

    settings = _settings(tmp_path)
    # settings.trend_template.sma_slow = 10, so "sma200" = SMA(close, 10)
    # Last 10 bars = dates_list[310:320] = 5 pre-split (raw=200) + 5 post-split (raw=100)

    prepared_adj = _prepare_frame(frame, settings, corp_actions=corp_actions)
    prepared_raw = _prepare_frame(frame, settings)

    last_row_adj = prepared_adj.filter(pl.col("date") == dates_list[-1]).select("sma200").item()
    last_row_raw = prepared_raw.filter(pl.col("date") == dates_list[-1]).select("sma200").item()

    # With adjustment: all closes become 100 (pre-split 200*0.5=100, post-split 100*1.0=100)
    # So SMA(10) = 100
    assert last_row_adj == pytest.approx(100.0, rel=0.02)
    # Without adjustment: last 10 raw closes = [200,200,200,200,200, 100,100,100,100,100]
    # SMA(10) = (5*200 + 5*100) / 10 = 150
    assert last_row_raw == pytest.approx(150.0, rel=0.02)
