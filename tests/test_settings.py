from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sepa_scanner.settings import load_settings


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    config_path = tmp_path / "config"
    config_path.mkdir()

    (config_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "market": {
                    "timezone": "Asia/Kolkata",
                    "benchmark_symbol": "NIFTYBEES",
                    "scan_time_ist": "16:10",
                },
                "data": {
                    "redownload_days": 5,
                    "min_history_days": 320,
                    "bhavcopy_api_key": "favCapital",
                    "http_timeout_sec": 30,
                    "max_retries": 5,
                },
                "universe": {
                    "allowed_series": ["EQ"],
                    "min_price_inr": 50,
                    "min_adv50_inr": 250000000,
                    "min_avg_volume_shares": 200000,
                    "max_symbols": 1500,
                },
                "trend_template": {
                    "sma_fast": 50,
                    "sma_mid": 150,
                    "sma_slow": 200,
                    "lookback_52w": 252,
                    "sma200_slope_lookback": 22,
                    "min_above_52w_low_pct": 0.30,
                    "max_below_52w_high_pct": 0.25,
                },
                "relative_strength": {
                    "lookbacks": [63, 126, 189, 252],
                    "weights": [0.4, 0.2, 0.2, 0.2],
                    "rs_rank_min": 70,
                },
                "setup_vcp": {
                    "base_lookback": 65,
                    "max_range10_pct": 0.10,
                    "max_vol_dryup_ratio": 0.60,
                    "atr_period": 14,
                    "atr_contraction_factor": 0.75,
                    "max_extension_from_50sma_pct": 0.10,
                    "base_max_depth_pct": 0.35,
                    "pre_alert_proximity_pct": 0.03,
                    "contraction_min_count": 2,
                },
                "market_filter": {
                    "benchmark_ema_period": 21,
                    "breadth_strong_pct": 0.60,
                    "breadth_weak_pct": 0.40,
                },
                "breakout": {
                    "require_market_filter": True,
                    "breakout_buffer_pct": 0.003,
                    "breakout_vol_mult": 1.5,
                },
                "risk": {
                    "entry_buffer_pct": 0.003,
                    "max_stop_pct": 0.08,
                },
                "alerts": {
                    "max_alerts_per_day": 10,
                    "send_watch_alerts": True,
                    "send_breakout_alerts": True,
                    "channels": ["telegram", "console"],
                },
            }
        ),
        encoding="utf-8",
    )

    (config_path / "alert_channels.yaml").write_text(
        yaml.safe_dump(
            {
                "telegram": {"enabled": True},
                "console": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )
    (config_path / "universe.yaml").write_text(
        yaml.safe_dump({"symbols": []}),
        encoding="utf-8",
    )

    return config_path


def test_load_settings_applies_defaults_and_env_overrides(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "sepa-data"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-id")
    monkeypatch.setenv("POLARS_MAX_THREADS", "6")
    monkeypatch.setenv("SEPA_DATA_DIR", str(data_dir))

    settings = load_settings(config_dir=config_dir)

    assert settings.market.benchmark_symbol == "NIFTYBEES"
    assert settings.alerts.channels == ["telegram", "console"]
    assert settings.telegram.bot_token == "bot-token"
    assert settings.telegram.chat_id == "chat-id"
    assert settings.runtime.polars_max_threads == 6
    assert settings.runtime.data_dir == data_dir
    assert settings.watchlist.max_candidates == 300


def test_load_settings_keeps_telegram_optional(config_dir: Path) -> None:
    settings = load_settings(config_dir=config_dir)

    assert settings.telegram.enabled is False
    assert settings.telegram.bot_token is None
    assert settings.telegram.chat_id is None
    assert settings.watchlist.max_candidates == 300
