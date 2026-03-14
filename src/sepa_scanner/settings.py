from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class MarketSettings(BaseModel):
    timezone: str
    benchmark_symbol: str
    scan_time_ist: str


class DataSettings(BaseModel):
    redownload_days: int
    min_history_days: int
    bhavcopy_api_key: str
    http_timeout_sec: int
    max_retries: int
    apply_corporate_actions_adjustment: bool = False


class UniverseSettings(BaseModel):
    allowed_series: list[str]
    min_price_inr: float
    min_adv50_inr: float
    min_avg_volume_shares: int
    max_symbols: int


class TrendTemplateSettings(BaseModel):
    sma_fast: int
    sma_mid: int
    sma_slow: int
    lookback_52w: int
    sma200_slope_lookback: int
    min_above_52w_low_pct: float
    max_below_52w_high_pct: float


class RelativeStrengthSettings(BaseModel):
    lookbacks: list[int]
    weights: list[float]
    rs_rank_min: float
    skip_recent_days: int = 0  # skip most recent month to avoid reversal effect

    @model_validator(mode="after")
    def validate_weights(self) -> "RelativeStrengthSettings":
        if len(self.lookbacks) != len(self.weights):
            raise ValueError("relative_strength.lookbacks and weights must match")
        if not self.weights:
            raise ValueError("relative_strength.weights cannot be empty")
        if round(sum(self.weights), 8) != 1.0:
            raise ValueError("relative_strength.weights must sum to 1.0")
        return self


class SetupVcpSettings(BaseModel):
    base_lookback: int
    max_range10_pct: float
    max_vol_dryup_ratio: float
    atr_period: int
    atr_contraction_factor: float
    max_extension_from_50sma_pct: float
    base_max_depth_pct: float
    pre_alert_proximity_pct: float
    contraction_smooth_window: int = 5
    contraction_min_peak_pct: float = 0.02
    contraction_min_count: int = 2
    contraction_standard_count: int = 3  # for high-conviction signals


class BreakoutSettings(BaseModel):
    require_market_filter: bool
    breakout_buffer_pct: float  # kept as fallback
    breakout_buffer_atr_mult: float | None = None  # when set, overrides breakout_buffer_pct
    breakout_vol_mult: float
    max_extension_breakout_pct: float = 0.15


class RiskSettings(BaseModel):
    entry_buffer_pct: float
    entry_buffer_atr_mult: float | None = None
    max_stop_pct: float
    target_r: float | None = None


class AlertsSettings(BaseModel):
    max_alerts_per_day: int
    send_watch_alerts: bool
    send_breakout_alerts: bool
    channels: list[str]


class WatchlistSettings(BaseModel):
    max_candidates: int = 300

    @field_validator("max_candidates")
    @classmethod
    def validate_max_candidates(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("watchlist.max_candidates must be positive")
        return value


class MarketFilterSettings(BaseModel):
    benchmark_ema_period: int = 21
    breadth_strong_pct: float = 0.60
    breadth_weak_pct: float = 0.40


class TelegramSettings(BaseModel):
    bot_token: str | None = None
    chat_id: str | None = None
    enabled: bool = False

    @model_validator(mode="after")
    def set_enabled_flag(self) -> "TelegramSettings":
        self.enabled = bool(self.bot_token and self.chat_id)
        return self


class RuntimeSettings(BaseModel):
    data_dir: Path
    polars_max_threads: int = Field(default_factory=lambda: os.cpu_count() or 4)

    @field_validator("polars_max_threads")
    @classmethod
    def validate_threads(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("POLARS_MAX_THREADS must be positive")
        return value


class AppSettings(BaseModel):
    base_dir: Path
    config_dir: Path
    market: MarketSettings
    data: DataSettings
    universe: UniverseSettings
    trend_template: TrendTemplateSettings
    relative_strength: RelativeStrengthSettings
    setup_vcp: SetupVcpSettings
    breakout: BreakoutSettings
    risk: RiskSettings
    alerts: AlertsSettings
    watchlist: WatchlistSettings = Field(default_factory=WatchlistSettings)
    market_filter: MarketFilterSettings = Field(default_factory=MarketFilterSettings)
    telegram: TelegramSettings
    runtime: RuntimeSettings
    alert_channels: dict[str, Any] = Field(default_factory=dict)
    manual_universe: list[str] = Field(default_factory=list)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_settings(config_dir: Path | None = None) -> AppSettings:
    resolved_config_dir = (config_dir or Path("config")).resolve()
    base_dir = resolved_config_dir.parent
    dotenv = _load_dotenv(base_dir / ".env")
    config_values = _read_yaml(resolved_config_dir / "config.yaml")
    alert_channels = _read_yaml(resolved_config_dir / "alert_channels.yaml")
    universe_file = _read_yaml(resolved_config_dir / "universe.yaml")

    def env_value(name: str, default: str | None = None) -> str | None:
        return os.getenv(name, dotenv.get(name, default))

    runtime = RuntimeSettings(
        data_dir=Path(env_value("SEPA_DATA_DIR", str(base_dir / "data"))).resolve(),
        polars_max_threads=int(env_value("POLARS_MAX_THREADS") or os.cpu_count() or 4),
    )
    telegram = TelegramSettings(
        bot_token=env_value("TELEGRAM_BOT_TOKEN"),
        chat_id=env_value("TELEGRAM_CHAT_ID"),
    )

    return AppSettings(
        base_dir=base_dir,
        config_dir=resolved_config_dir,
        market=MarketSettings.model_validate(config_values["market"]),
        data=DataSettings.model_validate(config_values["data"]),
        universe=UniverseSettings.model_validate(config_values["universe"]),
        trend_template=TrendTemplateSettings.model_validate(config_values["trend_template"]),
        relative_strength=RelativeStrengthSettings.model_validate(
            config_values["relative_strength"]
        ),
        setup_vcp=SetupVcpSettings.model_validate(config_values["setup_vcp"]),
        breakout=BreakoutSettings.model_validate(config_values["breakout"]),
        risk=RiskSettings.model_validate(config_values["risk"]),
        alerts=AlertsSettings.model_validate(config_values["alerts"]),
        watchlist=WatchlistSettings.model_validate(config_values.get("watchlist", {})),
        market_filter=MarketFilterSettings.model_validate(config_values.get("market_filter", {})),
        telegram=telegram,
        runtime=runtime,
        alert_channels=alert_channels,
        manual_universe=list(universe_file.get("symbols", [])),
    )
