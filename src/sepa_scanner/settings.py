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
    bhavcopy_api_key: str = "favCapital"
    http_timeout_sec: int
    max_retries: int
    # Price panel source. "yfinance" = legacy survivorship-biased panel (default);
    # "nse_bhavcopy" = survivorship-free point-in-time raw-price panel (data/pit).
    source: str = "yfinance"

    @property
    def panel_subdir(self) -> str:
        return "pit" if self.source == "nse_bhavcopy" else "yf_adjusted"


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
    rs_rank_max: float = 100.0 # New: Hard ceiling
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
    contraction_max_count: int = 99  # config lowers this when exhaustion filtering is enabled
    contraction_standard_count: int = 3  # for high-conviction signals
    require_progressive: bool = False  # gate setup on true VCP progressive narrowing (each contraction >=20% tighter)


class BreakoutSettings(BaseModel):
    require_market_filter: bool
    breakout_buffer_pct: float  # kept as fallback
    breakout_buffer_atr_mult: float | None = None  # when set, overrides breakout_buffer_pct
    breakout_vol_mult: float
    breakout_vol_max_mult: float = 999.0 # New: Hard ceiling
    max_extension_breakout_pct: float = 0.15


class PullbackSettings(BaseModel):
    enabled: bool = False
    rs_pullback_min: float = 70.0
    leader_min_days: int = 20
    anchor_ma: str = "ema21"
    ma_touch_pct: float = 0.02
    pullback_voldry_max: float = 0.90
    reversal_upper_range_frac: float = 2.0 / 3.0
    dedup_days: int = 10

    @field_validator("anchor_ma")
    @classmethod
    def validate_anchor_ma(cls, value: str) -> str:
        if value not in {"ema21", "sma50"}:
            raise ValueError("pullback.anchor_ma must be 'ema21' or 'sma50'")
        return value

    @field_validator("leader_min_days", "dedup_days")
    @classmethod
    def validate_positive_days(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("pullback day windows must be positive")
        return value

    @field_validator(
        "ma_touch_pct",
        "pullback_voldry_max",
        "reversal_upper_range_frac",
    )
    @classmethod
    def validate_positive_ratio(cls, value: float) -> float:
        if value <= 0.0:
            raise ValueError("pullback ratios must be positive")
        return value

    @field_validator("reversal_upper_range_frac")
    @classmethod
    def validate_range_fraction(cls, value: float) -> float:
        if value > 1.0:
            raise ValueError("pullback.reversal_upper_range_frac must be <= 1")
        return value


class RiskSettings(BaseModel):
    entry_buffer_pct: float
    entry_buffer_atr_mult: float | None = None
    max_stop_pct: float
    struct_stop_atr_mult: float | None = None  # New: multiplier for ATR-based stop
    target_r: float | None = None


class BacktestSettings(BaseModel):
    pending_days: int = 8
    max_hold_days: int = 150
    exit_mode: str = "advanced_sepa"
    baseline_tighten_r: float = 7.0
    stop_trigger_mode: str = "close_only"
    signal_exit_execution: str = "same_close"
    slippage_bps: float = 10.0
    rs_rank_min: float | None = None
    rs_rank_max: float | None = None
    signal_ranking_mode: str = "default"
    max_signals_per_day: int | None = None
    signal_quality_profile: str = "default"
    signal_quality_range10_min: float | None = None
    signal_quality_range10_max: float | None = None
    signal_quality_voldry_min: float | None = None
    signal_quality_voldry_max: float | None = None
    signal_quality_contraction_min: int | None = None
    signal_quality_contraction_max: int | None = None
    signal_quality_setup_streak_min: int | None = None
    signal_quality_setup_streak_max: int | None = None
    signal_quality_pivot_age_min: int | None = None
    signal_quality_pivot_age_max: int | None = None
    signal_quality_vol_mult_min: float | None = None
    signal_quality_offense_score_min: float | None = None
    signal_quality_offense_score_max: float | None = None
    signal_quality_size_tilt_mode: str = "off"
    signal_quality_size_tilt_score_min: float = 5.5
    signal_quality_size_tilt_score_max: float = 8.0
    signal_quality_size_tilt_min_mult: float = 0.60
    signal_quality_size_tilt_max_mult: float = 1.40
    use_extended_exit_model: bool = False
    use_regime_entry_controls: bool = False
    sector_alpha_enabled: bool = True
    skip_months: list[int] = []  # months to skip entirely, e.g. [1] for January
    sector_alpha_threshold_high: float = 85.0
    sector_alpha_threshold_low: float = 60.0
    sector_alpha_mult_high: float = 1.2
    sector_alpha_mult_low: float = 0.8


class ExitStrategySettings(BaseModel):
    breakeven_activation_r: float = 1.0
    partial_profit_r: float = 2.0
    partial_profit_pct: float = 0.50
    trail_activation_r: float = 1.5
    max_loss_pct: float = 0.08
    absolute_max_loss_pct: float = 0.10
    trail_mode: str = "adaptive"
    trail_pct: float = 0.10
    trail_pct_bull: float = 0.25
    trail_pct_bear: float = 0.15
    trail_widen_r: float = 3.0
    dead_money_days: int | None = 17
    dead_money_extend_days: int = 0
    dead_money_min_open_r: float = 0.0
    dead_money_min_mfe_r: float = 0.75
    dead_money_extend_to_breakeven: bool = False
    stagnation_exit_bars: int | None = None
    stagnation_max_mfe_r: float = 0.40
    stagnation_max_open_r: float = 0.0
    time_stop_days: int | None = 15
    max_hold_before_tighten: int = 40
    climax_min_hold_days: int = 15
    climax_conditions_required: int = 3
    soft_expiry_days: int = 30
    soft_expiry_gain: float = 0.20


class FundamentalGateSettings(BaseModel):
    """Experimental earnings-growth gate on breakout signals (default off).

    Corrected testing on the partial local data did not improve the canonical
    strategy. Keep this off until survivorship-clean filings with actual NSE
    dissemination dates are available.
    """
    enabled: bool = False
    data_path: str = "data/reference/screener_annual.csv"
    freq: str | None = "annual"          # filter the data file's freq column if present
    min_yoy_growth: float = 0.25         # Minervini "code 33"; cohort ~median
    reporting_lag_days: int = 75         # legacy research fallback; prohibited when strict
    pass_if_missing: bool = True         # keep signals with no point-in-time fundamental data
    availability_date_column: str = "available_date"
    require_actual_announcement_date: bool = True


class AlertsSettings(BaseModel):
    max_alerts_per_day: int
    signal_ranking_mode: str = "default"
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


class PortfolioSettings(BaseModel):
    max_heat_pct: float = 0.25           # max 25% total open risk
    max_positions: int = 20              # max concurrent positions
    max_sector_positions: int = 10       # max per sector
    regime_heat_mult_strong: float = 1.0 # New: multiplier for STRONG regimes
    max_sector_pct: float = 0.60         # max 60% in any one sector
    drawdown_half_pct: float = 0.15      # halve sizes at -15% from peak
    drawdown_stop_pct: float = 0.25      # stop new entries at -25%
    drawdown_close_pct: float = 0.35     # close all at -35%
    initial_capital: float = 1_000_000.0
    risk_per_trade_pct: float = 0.035    # 3.5% risk per trade
    max_name_pct: float = 0.20           # hard cap: single position <= 20% of equity
    cash_yield_apy: float = 0.0          # strategy metrics exclude assumed idle-cash income
    regime_gate_enabled: bool = True
    regime_high_volatility_threshold: float = 0.25  # 20D ann. realized vol on benchmark
    regime_cooldown_days: int = 0
    breadth_soft_floor_pct: float | None = None
    breadth_soft_size_mult: float = 0.50
    pyramid_max_adds: int = 0
    pyramid_trigger_r: float = 1.0
    pyramid_add_interval_r: float = 0.5
    pyramid_risk_frac: float = 0.50


class IndiaFilterSettings(BaseModel):
    prefer_fno: bool = True                    # prefer F&O stocks
    exclude_asm_gsm: bool = True               # exclude ASM/GSM stocks
    min_market_cap_cr: float = 500.0           # ₹500Cr minimum
    min_adtv_cr: float = 5.0                   # ₹5Cr minimum ADTV
    fno_list_path: str | None = None           # path to local F&O list CSV
    asm_gsm_list_path: str | None = None       # path to local ASM/GSM list CSV
    sector_map_path: str | None = None         # path to sector classification CSV


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
    pullback: PullbackSettings = Field(default_factory=PullbackSettings)
    risk: RiskSettings
    backtest: BacktestSettings = Field(default_factory=BacktestSettings)
    exit_strategy: ExitStrategySettings = Field(default_factory=ExitStrategySettings)
    alerts: AlertsSettings
    watchlist: WatchlistSettings = Field(default_factory=WatchlistSettings)
    market_filter: MarketFilterSettings = Field(default_factory=MarketFilterSettings)
    portfolio: PortfolioSettings = Field(default_factory=PortfolioSettings)
    fundamental_gate: FundamentalGateSettings = Field(default_factory=FundamentalGateSettings)
    india_filter: IndiaFilterSettings | None = None
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


def _default_config_dir() -> Path:
    """Resolve config/ relative to the project root, not CWD.

    Walks up from this file's location (src/sepa_scanner/settings.py)
    to find the directory containing config/config.yaml.
    """
    anchor = Path(__file__).resolve().parent  # src/sepa_scanner/
    for parent in (anchor, anchor.parent, anchor.parent.parent):
        candidate = parent / "config" / "config.yaml"
        if candidate.exists():
            return parent / "config"
    # Fall back to CWD-relative (original behavior)
    return Path("config").resolve()


def load_settings(config_dir: Path | None = None) -> AppSettings:
    resolved_config_dir = (config_dir or _default_config_dir()).resolve()
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
        pullback=PullbackSettings.model_validate(config_values.get("pullback", {})),
        risk=RiskSettings.model_validate(config_values["risk"]),
        backtest=BacktestSettings.model_validate(config_values.get("backtest", {})),
        exit_strategy=ExitStrategySettings.model_validate(config_values.get("exit_strategy", {})),
        alerts=AlertsSettings.model_validate(config_values["alerts"]),
        watchlist=WatchlistSettings.model_validate(config_values.get("watchlist", {})),
        market_filter=MarketFilterSettings.model_validate(config_values.get("market_filter", {})),
        portfolio=PortfolioSettings.model_validate(config_values.get("portfolio", {})),
        fundamental_gate=FundamentalGateSettings.model_validate(config_values.get("fundamental_gate", {})),
        telegram=telegram,
        runtime=runtime,
        alert_channels=alert_channels,
        manual_universe=list(universe_file.get("symbols", [])),
    )
