from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import polars as pl

from sepa_scanner.data_sources.corporate_actions import (
    build_adjusted_ohlcv,
    compute_adjustment_factors,
)
from sepa_scanner.indicators.atr import add_atr_columns
from sepa_scanner.indicators.contraction import add_contraction_columns
from sepa_scanner.indicators.highs_lows import add_pivot_column, rolling_high_expr, rolling_low_expr
from sepa_scanner.indicators.rs import compute_rs_ranks, precompute_rs_scores, rank_rs_for_date
from sepa_scanner.indicators.sma import sma_expr
from sepa_scanner.indicators.tightness import add_tightness_columns
from sepa_scanner.indicators.volume import add_volume_columns
from sepa_scanner.settings import AppSettings
from sepa_scanner.strategy.market_filter import market_filter_on
from sepa_scanner.strategy.ranking import rank_signals
from sepa_scanner.strategy.trend_template import add_trend_template_columns
from sepa_scanner.strategy.vcp_setup import add_setup_columns

WATCH_FAIL_TT = 1
WATCH_FAIL_RS = 2
WATCH_FAIL_SETUP = 4
WATCH_FAIL_ABOVE_PIVOT = 8
WATCH_FAIL_TOO_FAR = 16
WATCH_FAIL_IS_BREAKOUT = 32

BREAKOUT_FAIL_TT = 1
BREAKOUT_FAIL_RS = 2
BREAKOUT_FAIL_SETUP_PREV = 4
BREAKOUT_FAIL_MARKET_FILTER = 8
BREAKOUT_FAIL_BELOW_PIVOT = 16
BREAKOUT_FAIL_VOL_LOW = 32
BREAKOUT_FAIL_NOT_FIRST_DAY = 64
BREAKOUT_FAIL_OVEREXTENDED = 128


@dataclass(slots=True)
class ScanResult:
    scan_date: date
    market_filter_on: bool
    num_symbols: int
    num_eligible: int
    signals: pl.DataFrame
    watchlist_candidates: pl.DataFrame = field(default_factory=pl.DataFrame)
    diagnostics: pl.DataFrame = field(default_factory=pl.DataFrame)
    num_trend_pass: int = 0
    num_rs_pass: int = 0
    num_setup_pass: int = 0
    num_near_pivot: int = 0


def compute_symbol_diagnostics(
    ohlcv: pl.DataFrame,
    settings: AppSettings,
    scan_date: date,
) -> dict[str, object]:
    """Compute all indicator values and per-condition results for a single symbol.

    Accepts the single-symbol OHLCV frame already loaded for the chart
    (columns: date, symbol, open, high, low, close, volume).
    Returns a dict with raw indicator values and individual boolean conditions
    so the explainer can render a full condition breakdown.
    """
    trend = settings.trend_template
    setup = settings.setup_vcp

    df = ohlcv.sort(["symbol", "date"])
    if "turnover" not in df.columns:
        df = df.with_columns(
            (pl.col("close") * pl.col("volume")).alias("turnover")
        )

    df = df.with_columns(
        sma_expr("close", trend.sma_fast).over("symbol").alias("sma50"),
        sma_expr("close", trend.sma_mid).over("symbol").alias("sma150"),
        sma_expr("close", trend.sma_slow).over("symbol").alias("sma200"),
        rolling_high_expr("high", trend.lookback_52w).over("symbol").alias("high52"),
        rolling_low_expr("low", trend.lookback_52w).over("symbol").alias("low52"),
    ).with_columns(
        pl.col("sma200")
        .shift(trend.sma200_slope_lookback)
        .over("symbol")
        .alias("sma200_lag20")
    )

    df = add_volume_columns(df)
    df = add_tightness_columns(df, setup.base_lookback)
    df = add_contraction_columns(
        df, setup.base_lookback, setup.contraction_smooth_window, setup.contraction_min_peak_pct
    )
    df = add_pivot_column(df, setup.base_lookback)
    df = add_atr_columns(df, period=setup.atr_period)
    df = df.with_columns(
        sma_expr("atr_pct", 10).over("symbol").alias("atr_pct_sma10"),
        sma_expr("atr_pct", 20).over("symbol").alias("atr_pct_sma20"),
        sma_expr("atr_pct", 30).over("symbol").alias("atr_pct_sma30"),
        pl.col("atr_pct").shift(20).over("symbol").alias("atr_pct_lag20"),
    )

    df = add_trend_template_columns(df, trend)
    df = add_setup_columns(df, setup)

    row = df.filter(pl.col("date") == pl.lit(scan_date))
    if row.is_empty():
        row = df.tail(1)
    if row.is_empty():
        return {}

    return row.to_dicts()[0]


def _prepare_frame(frame: pl.DataFrame, settings: AppSettings, corp_actions: pl.DataFrame | None = None) -> pl.DataFrame:
    # Apply corporate actions adjustment if provided.
    # All rolling indicators must be computed on adjusted prices.
    # raw_close and raw_volume are preserved for signal-day display.
    if corp_actions is not None and not corp_actions.is_empty():
        adj_factors = compute_adjustment_factors(frame.lazy(), corp_actions)
        frame = build_adjusted_ohlcv(frame.lazy(), adj_factors).collect()

    trend = settings.trend_template
    setup = settings.setup_vcp

    prepared = frame.sort(["symbol", "date"]).with_columns(
        sma_expr("close", trend.sma_fast).over("symbol").alias("sma50"),
        sma_expr("close", trend.sma_mid).over("symbol").alias("sma150"),
        sma_expr("close", trend.sma_slow).over("symbol").alias("sma200"),
        rolling_high_expr("high", trend.lookback_52w).over("symbol").alias("high52"),
        rolling_low_expr("low", trend.lookback_52w).over("symbol").alias("low52"),
        pl.col("close").cum_count().over("symbol").alias("history_count"),
    ).with_columns(
        pl.col("sma200")
        .shift(trend.sma200_slope_lookback)
        .over("symbol")
        .alias("sma200_lag20")
    )

    prepared = add_volume_columns(prepared)
    prepared = add_tightness_columns(prepared, setup.base_lookback)
    prepared = add_contraction_columns(
        prepared, setup.base_lookback, setup.contraction_smooth_window, setup.contraction_min_peak_pct
    )
    prepared = add_pivot_column(prepared, setup.base_lookback)
    prepared = add_atr_columns(prepared, period=setup.atr_period)
    prepared = prepared.with_columns(
        sma_expr("atr_pct", 10).over("symbol").alias("atr_pct_sma10"),
        sma_expr("atr_pct", 20).over("symbol").alias("atr_pct_sma20"),
        sma_expr("atr_pct", 30).over("symbol").alias("atr_pct_sma30"),
        pl.col("atr_pct").shift(20).over("symbol").alias("atr_pct_lag20"),
        pl.col("low").rolling_min(window_size=10, min_samples=10).over("symbol").alias("struct_stop"),
        pl.col("low").rolling_min(window_size=10, min_samples=10).shift(1).over("symbol").alias("struct_stop_prev"),
        ((pl.col("pivot") - pl.col("close")) / pl.col("pivot")).alias("near_pivot"),
        (pl.col("volume") / pl.col("vol50")).alias("vol_mult"),
        pl.col("close").shift(1).over("symbol").alias("prev_close"),
        pl.col("range10").shift(1).over("symbol").alias("range10_prev"),
        pl.col("voldry").shift(1).over("symbol").alias("voldry_prev"),
        pl.col("atr_pct").shift(1).over("symbol").alias("atr_pct_prev"),
    )
    prepared = add_trend_template_columns(prepared, trend)
    prepared = add_setup_columns(prepared, setup).with_columns(
        pl.col("setup_pass").shift(1).over("symbol").alias("setup_pass_prev")
    )

    ema_period = settings.market_filter.benchmark_ema_period
    prepared = prepared.with_columns(
        pl.col("close")
        .ewm_mean(span=ema_period, adjust=False)
        .over("symbol")
        .alias("benchmark_ema"),
    )
    return prepared


def _eligible_universe(prepared: pl.DataFrame, settings: AppSettings, scan_date: date) -> pl.DataFrame:
    universe = settings.universe
    benchmark_symbol = settings.market.benchmark_symbol

    return (
        prepared.filter(pl.col("date") == pl.lit(scan_date))
        .filter(pl.col("history_count") >= settings.data.min_history_days)
        .filter(pl.col("close") >= universe.min_price_inr)
        .filter(pl.col("adv50") >= universe.min_adv50_inr)
        .filter(pl.col("vol50") >= universe.min_avg_volume_shares)
        .filter(pl.col("symbol") != benchmark_symbol)
        .sort("adv50", descending=True)
        .head(universe.max_symbols)
    )


def _add_lifecycle_columns(prepared: pl.DataFrame) -> pl.DataFrame:
    """Add setup_active_streak and pivot_age_days via cumulative group trick."""
    return prepared.with_columns(
        (~pl.col("setup_pass").fill_null(False))
        .cum_sum()
        .over("symbol")
        .alias("_setup_group"),
        (pl.col("pivot") != pl.col("pivot").shift(1).over("symbol"))
        .fill_null(True)
        .cum_sum()
        .over("symbol")
        .alias("_pivot_group"),
    ).with_columns(
        pl.col("setup_pass")
        .fill_null(False)
        .cast(pl.Int32)
        .cum_sum()
        .over("symbol", "_setup_group")
        .alias("setup_active_streak"),
        pl.len()
        .over("symbol", "_pivot_group")
        .sub(1)
        .alias("_pivot_group_len"),
    ).with_columns(
        pl.col("_pivot_group_len")
        .cum_count()
        .over("symbol", "_pivot_group")
        .sub(1)
        .alias("pivot_age_days"),
    ).drop("_setup_group", "_pivot_group", "_pivot_group_len")


def _build_diagnostics(
    scan_rows: pl.DataFrame,
    lifecycle_slice: pl.DataFrame,
    settings: AppSettings,
    market_on: bool,
    scan_date: date,
) -> pl.DataFrame:
    """Build the daily_diagnostics DataFrame from scan_rows + lifecycle columns."""
    breakout_buffer = 1.0 + settings.breakout.breakout_buffer_pct

    diag = scan_rows.join(
        lifecycle_slice.select("symbol", "setup_active_streak", "pivot_age_days"),
        on="symbol",
        how="left",
    )

    diag = diag.with_columns(
        # WATCH fail mask
        (
            pl.when(~pl.col("trend_template_pass")).then(WATCH_FAIL_TT).otherwise(0)
            + pl.when(~pl.col("rs_pass").fill_null(False)).then(WATCH_FAIL_RS).otherwise(0)
            + pl.when(~pl.col("setup_pass").fill_null(False)).then(WATCH_FAIL_SETUP).otherwise(0)
            + pl.when(pl.col("near_pivot") < 0).then(WATCH_FAIL_ABOVE_PIVOT).otherwise(0)
            + pl.when(
                pl.col("near_pivot") > settings.setup_vcp.pre_alert_proximity_pct
            ).then(WATCH_FAIL_TOO_FAR).otherwise(0)
        ).cast(pl.Int32).alias("watch_fail_mask"),
        # BREAKOUT fail mask
        (
            pl.when(~pl.col("trend_template_pass")).then(BREAKOUT_FAIL_TT).otherwise(0)
            + pl.when(~pl.col("rs_pass").fill_null(False)).then(BREAKOUT_FAIL_RS).otherwise(0)
            + pl.when(~pl.col("setup_pass_prev").fill_null(False)).then(BREAKOUT_FAIL_SETUP_PREV).otherwise(0)
            + pl.when(
                pl.lit(settings.breakout.require_market_filter) & ~pl.lit(market_on)
            ).then(BREAKOUT_FAIL_MARKET_FILTER).otherwise(0)
            + pl.when(
                pl.col("close") < pl.col("pivot") * breakout_buffer
            ).then(BREAKOUT_FAIL_BELOW_PIVOT).otherwise(0)
            + pl.when(
                pl.col("volume") < pl.col("vol50") * settings.breakout.breakout_vol_mult
            ).then(BREAKOUT_FAIL_VOL_LOW).otherwise(0)
            + pl.when(
                pl.col("prev_close") >= pl.col("pivot") * breakout_buffer
            ).then(BREAKOUT_FAIL_NOT_FIRST_DAY).otherwise(0)
            + pl.when(
                pl.col("close") > pl.col("sma50") * (1.0 + settings.breakout.max_extension_breakout_pct)
            ).then(BREAKOUT_FAIL_OVEREXTENDED).otherwise(0)
        ).cast(pl.Int32).alias("breakout_fail_mask"),
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    return diag.select(
        pl.lit(scan_date).alias("diag_date"),
        "symbol",
        pl.lit(True).alias("eligible"),
        pl.col("trend_template_pass").alias("tt_pass"),
        pl.col("tt_close_above_sma150").alias("tt1_close_above_sma150"),
        pl.col("tt_close_above_sma200").alias("tt2_close_above_sma200"),
        pl.col("tt_sma150_above_sma200").alias("tt3_sma150_above_sma200"),
        pl.col("tt_sma200_rising").alias("tt4_sma200_rising"),
        pl.col("tt_sma50_above_sma150").alias("tt5_sma50_above_sma150"),
        pl.col("tt_sma50_above_sma200").alias("tt6_sma50_above_sma200"),
        pl.col("tt_close_above_sma50").alias("tt7_close_above_sma50"),
        pl.col("tt_above_52w_low").alias("tt8_above_52w_low"),
        pl.col("tt_near_52w_high").alias("tt9_near_52w_high"),
        "rs_rank",
        pl.col("rs_pass").fill_null(False),
        "setup_pass",
        pl.col("range10_ok").alias("vcp1_range10_ok"),
        pl.col("voldry_ok").alias("vcp2_voldry_ok"),
        pl.col("atr_trend_ok").alias("vcp3_atr_trend_ok"),
        pl.col("atr_contract_ok").alias("vcp4_atr_contract_ok"),
        pl.col("extension_ok").alias("vcp5_extension_ok"),
        pl.col("base_depth_ok").alias("vcp6_base_depth_ok"),
        pl.col("contraction_count").fill_null(0).cast(pl.Int32),
        "contraction_quality",
        "pivot",
        "near_pivot",
        pl.col("setup_active_streak").fill_null(0).cast(pl.Int32),
        pl.col("pivot_age_days").fill_null(0).cast(pl.Int32),
        "watch_fail_mask",
        "breakout_fail_mask",
        pl.lit(now).alias("created_at"),
    )


def scan_for_date(
    frame: pl.DataFrame,
    settings: AppSettings,
    scan_date: date,
    *,
    _prepared: pl.DataFrame | None = None,
    _lifecycle: pl.DataFrame | None = None,
    _rs_scores: pl.DataFrame | None = None,
    corp_actions: pl.DataFrame | None = None,
) -> ScanResult:
    prepared = _prepared if _prepared is not None else _prepare_frame(frame, settings, corp_actions=corp_actions)

    # Compute benchmark EMA for scan_date
    bench_ema_row = prepared.filter(
        (pl.col("symbol") == settings.market.benchmark_symbol)
        & (pl.col("date") == pl.lit(scan_date))
    ).select("benchmark_ema").to_dicts()
    bench_ema_val = bench_ema_row[0]["benchmark_ema"] if bench_ema_row else None

    # Compute breadth: % of eligible stocks with close > sma50
    universe_rows = _eligible_universe(prepared, settings, scan_date)
    total_eligible = universe_rows.height
    if total_eligible > 0:
        above_sma50_count = universe_rows.filter(pl.col("close") > pl.col("sma50")).height
        breadth = above_sma50_count / total_eligible
    else:
        breadth = 0.0

    market_on = market_filter_on(
        prepared,
        settings.market.benchmark_symbol,
        scan_date,
        benchmark_ema=bench_ema_val,
        breadth_pct=breadth,
        require_breadth_pct=settings.market_filter.require_breadth_pct,
    )
    candidate_symbols = universe_rows["symbol"].to_list()

    if _rs_scores is not None:
        rs_ranks = rank_rs_for_date(_rs_scores, scan_date, candidate_symbols)
    else:
        rs_ranks = compute_rs_ranks(
            frame=prepared.select("date", "symbol", "close"),
            benchmark_symbol=settings.market.benchmark_symbol,
            lookbacks=settings.relative_strength.lookbacks,
            weights=settings.relative_strength.weights,
            scan_date=scan_date,
            symbols=candidate_symbols,
            skip_recent_days=settings.relative_strength.skip_recent_days,
        )
    rs_pass_symbols = set(
        rs_ranks.filter(pl.col("rs_rank") >= settings.relative_strength.rs_rank_min)["symbol"].to_list()
    )

    scan_rows = universe_rows.join(rs_ranks, on="symbol", how="left").with_columns(
        pl.col("symbol").is_in(list(rs_pass_symbols)).alias("rs_pass")
    )
    num_trend_pass = scan_rows.filter(pl.col("trend_template_pass")).height
    num_rs_pass = scan_rows.filter(pl.col("rs_pass").fill_null(False)).height
    num_setup_pass = scan_rows.filter(
        pl.col("trend_template_pass")
        & pl.col("rs_pass").fill_null(False)
        & pl.col("setup_pass").fill_null(False)
    ).height
    num_near_pivot = scan_rows.filter(
        pl.col("trend_template_pass")
        & pl.col("rs_pass").fill_null(False)
        & pl.col("setup_pass").fill_null(False)
        & (pl.col("near_pivot") >= 0)
        & (pl.col("near_pivot") <= settings.setup_vcp.pre_alert_proximity_pct)
    ).height
    watchlist_candidates = (
        scan_rows.filter(
            pl.col("trend_template_pass")
            & pl.col("rs_pass").fill_null(False)
            & pl.col("setup_pass").fill_null(False)
        )
        .select(
            pl.col("date").alias("watch_date"),
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
        )
        .sort(
            by=["near_pivot", "rs_rank", "adv50", "symbol"],
            descending=[False, True, True, False],
        )
        .head(settings.watchlist.max_candidates)
    )
    breakout_buffer = 1.0 + settings.breakout.breakout_buffer_pct
    breakout_mask = (
        pl.col("trend_template_pass")
        & pl.col("rs_pass")
        & pl.col("setup_pass_prev").fill_null(False)
        & (
            pl.lit(not settings.breakout.require_market_filter)
            | pl.lit(market_on)
        )
        & (pl.col("close") >= pl.col("pivot") * breakout_buffer)
        & (pl.col("volume") >= pl.col("vol50") * settings.breakout.breakout_vol_mult)
        & (pl.col("prev_close") < pl.col("pivot") * breakout_buffer)
        & (pl.col("close") <= pl.col("sma50") * (1.0 + settings.breakout.max_extension_breakout_pct))
    )
    watch_mask = (
        pl.col("trend_template_pass")
        & pl.col("rs_pass")
        & pl.col("setup_pass")
        & (pl.col("near_pivot") >= 0)
        & (pl.col("near_pivot") <= settings.setup_vcp.pre_alert_proximity_pct)
        & ~breakout_mask
    )

    enriched = scan_rows.with_columns(
        breakout_mask.alias("breakout_signal"),
        watch_mask.alias("watch_signal"),
        (pl.col("pivot") * (1.0 + settings.risk.entry_buffer_pct)).alias("entry_ref"),
    ).with_columns(
        (pl.col("entry_ref") * (1.0 - settings.risk.max_stop_pct)).alias("max_loss_stop"),
    ).with_columns(
        pl.coalesce(
            pl.max_horizontal("struct_stop", "max_loss_stop"),
            pl.col("max_loss_stop"),
        ).alias("stop"),
        pl.coalesce(
            pl.max_horizontal("struct_stop_prev", "max_loss_stop"),
            pl.col("max_loss_stop"),
        ).alias("stop_breakout"),
    ).with_columns(
        (pl.col("entry_ref") - pl.col("stop")).alias("r_per_share"),
        (pl.col("entry_ref") - pl.col("stop_breakout")).alias("r_per_share_breakout"),
    )

    # If adjusted, use raw_close for signal display fields
    if "raw_close" in enriched.columns:
        enriched = enriched.with_columns(pl.col("raw_close").alias("close"))

    _signal_cols = [
        pl.col("date").alias("signal_date"),
        "symbol",
        "signal_type",
        "close",
        "pivot",
        "near_pivot",
        "volume",
        "vol50",
        "vol_mult",
        "rs_rank",
        "range10",
        "voldry",
        "atr_pct",
        "adv50",
        "entry_ref",
        "stop",
        "r_per_share",
    ]

    breakout_signals = (
        enriched.filter(pl.col("breakout_signal") & (pl.col("r_per_share_breakout") > 0))
        .with_columns(
            pl.lit("BREAKOUT").alias("signal_type"),
            pl.col("range10_prev").alias("range10"),
            pl.col("voldry_prev").alias("voldry"),
            pl.col("atr_pct_prev").alias("atr_pct"),
            pl.col("stop_breakout").alias("stop"),
            pl.col("r_per_share_breakout").alias("r_per_share"),
        )
        .select(_signal_cols)
    )
    watch_signals = (
        enriched.filter(pl.col("watch_signal") & (pl.col("r_per_share") > 0))
        .with_columns(pl.lit("WATCH").alias("signal_type"))
        .select(_signal_cols)
    )

    signals = rank_signals(
        pl.concat([watch_signals, breakout_signals], how="vertical"),
        settings.alerts.max_alerts_per_day,
    )

    if _lifecycle is not None:
        lifecycle_slice = _lifecycle.filter(
            (pl.col("date") == pl.lit(scan_date))
            & pl.col("symbol").is_in(candidate_symbols)
        )
    else:
        lifecycle_prepared = _add_lifecycle_columns(
            prepared.filter(pl.col("symbol").is_in(candidate_symbols))
        )
        lifecycle_slice = lifecycle_prepared.filter(pl.col("date") == pl.lit(scan_date))
    diagnostics = _build_diagnostics(
        scan_rows, lifecycle_slice, settings, market_on, scan_date
    )

    return ScanResult(
        scan_date=scan_date,
        market_filter_on=market_on,
        num_symbols=prepared["symbol"].n_unique(),
        num_eligible=len(candidate_symbols),
        num_trend_pass=num_trend_pass,
        num_rs_pass=num_rs_pass,
        num_setup_pass=num_setup_pass,
        num_near_pivot=num_near_pivot,
        signals=signals,
        watchlist_candidates=watchlist_candidates,
        diagnostics=diagnostics,
    )


def scan_batch(
    frame: pl.DataFrame,
    settings: AppSettings,
    scan_dates: list[date],
    corp_actions: pl.DataFrame | None = None,
) -> Iterator[tuple[date, ScanResult | None, str | None]]:
    """Compute indicators once, then yield (scan_date, result, error) per date.

    Dramatically faster than calling scan_for_date per date because
    _prepare_frame, lifecycle columns, and RS scores are computed once.
    """
    prepared = _prepare_frame(frame, settings, corp_actions=corp_actions)
    lifecycle = _add_lifecycle_columns(prepared)
    rs_scores = precompute_rs_scores(
        prepared.select("date", "symbol", "close"),
        benchmark_symbol=settings.market.benchmark_symbol,
        lookbacks=settings.relative_strength.lookbacks,
        weights=settings.relative_strength.weights,
        skip_recent_days=settings.relative_strength.skip_recent_days,
    )

    for scan_date in scan_dates:
        try:
            result = scan_for_date(
                frame,
                settings,
                scan_date,
                _prepared=prepared,
                _lifecycle=lifecycle,
                _rs_scores=rs_scores,
            )
            yield (scan_date, result, None)
        except Exception as exc:
            yield (scan_date, None, str(exc))
