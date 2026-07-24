"""Research which breakout signal cohorts actually produce outsized winners."""

from __future__ import annotations

import json
import math
import sys
from bisect import bisect_left
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sepa_scanner.backtest.signal_scorecard import compute_scorecard, compute_signal_metrics
from sepa_scanner.settings import load_settings
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore
from sepa_scanner.strategy.ranking import load_sector_map
import portfolio_diagnostics as diag


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "db" / "sepa.duckdb"
OUTPUT_ROOT = DATA_DIR / "backtest" / "signal_quality_research"
OUTPUT_PATH = DATA_DIR / "backtest" / "signal_quality_research.json"
SIGNAL_PANEL_PATH = OUTPUT_ROOT / "signal_feature_panel.parquet"
COHORT_PATH = OUTPUT_ROOT / "feature_cohorts.csv"
CORRELATION_PATH = OUTPUT_ROOT / "feature_correlations.csv"
TRADE_PATH = OUTPUT_ROOT / "canonical_trade_outcomes.parquet"
EQUITY_PATH = OUTPUT_ROOT / "canonical_strategy_only_equity.parquet"
OPPORTUNITY_ROOT = Path(__file__).resolve().parent.parent / "reports" / "opportunity_ceiling"
FORWARD_DAYS = [1, 3, 5, 10, 20, 40, 60, 120]

RESEARCH_FEATURES = [
    "ret_63",
    "ret_126",
    "ret_252",
    "prior_ret_63",
    "momentum_acceleration_63",
    "vol_63_ann",
    "vol_126_ann",
    "vol_252_ann",
    "risk_adjusted_momentum_6_12",
    "distance_52w_high_pct",
    "distance_13w_high_pct",
    "positive_day_pct_63",
    "sma50_extension_pct",
    "breakout_gap_pct",
    "breakout_day_return_pct",
    "breakout_range_pct",
    "close_location_value",
    "turnover_20d_inr",
    "breadth_pct",
    "vol_20_ann",
    "rs_rank",
    "vol_mult",
    "range10",
    "voldry",
    "atr_pct",
    "contraction_count",
    "contraction_quality",
    "pivot_age_days",
    "offense_v2_score",
]

SECTIONS = [
    ("overall", "Overall"),
    ("by_rs_rank", "RS Rank"),
    ("by_range10", "Tightness"),
    ("by_voldry", "Volume Dry-up"),
    ("by_contraction", "Contraction Count"),
    ("by_contraction_quality", "Contraction Quality"),
    ("by_setup_streak", "Setup Streak"),
    ("by_pivot_age", "Pivot Age"),
    ("by_near_pivot", "Near Pivot"),
    ("by_atr_pct", "ATR %"),
    ("by_offense_v2_score", "Offense V2 Score"),
]


def _bucket_label(row: dict) -> str:
    for key, value in row.items():
        if key.endswith("_bucket"):
            return str(value)
    return "all"


def _sort_key(row: dict) -> tuple[float, float, float]:
    return (
        float(row.get("reached_3r_pct") or 0.0),
        float(row.get("reached_2r_pct") or 0.0),
        float(row.get("avg_mfe_r") or 0.0),
    )


def _print_row(label: str, row: dict) -> None:
    print(
        f"  {label:>12} | n={int(row.get('count', 0)):>4}"
        f" | 20d avg={float(row.get('avg_20d_return') or 0.0):>6.2f}%"
        f" | avg MFE={float(row.get('avg_mfe_r') or 0.0):>5.2f}R"
        f" | 2R={float(row.get('reached_2r_pct') or 0.0):>5.1f}%"
        f" | 3R={float(row.get('reached_3r_pct') or 0.0):>5.1f}%"
        f" | stop-first={float(row.get('stop_hit_first_pct') or 0.0):>5.1f}%"
    )


def _build_price_features(ohlcv: pl.DataFrame) -> pl.DataFrame:
    """Build point-in-time price, volatility, and breakout-bar features."""
    ordered = ohlcv.sort(["symbol", "date"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("_prev_close"),
        (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("_daily_ret"),
    )
    features = ordered.with_columns(
        (pl.col("close") / pl.col("close").shift(63).over("symbol") - 1.0).alias("ret_63"),
        (pl.col("close") / pl.col("close").shift(126).over("symbol") - 1.0).alias("ret_126"),
        (pl.col("close") / pl.col("close").shift(252).over("symbol") - 1.0).alias("ret_252"),
        (
            pl.col("close").shift(63).over("symbol")
            / pl.col("close").shift(126).over("symbol")
            - 1.0
        ).alias("prior_ret_63"),
        pl.col("_daily_ret")
        .rolling_std(window_size=63, min_samples=42)
        .over("symbol")
        .mul(math.sqrt(252.0))
        .alias("vol_63_ann"),
        pl.col("_daily_ret")
        .rolling_std(window_size=126, min_samples=84)
        .over("symbol")
        .mul(math.sqrt(252.0))
        .alias("vol_126_ann"),
        pl.col("_daily_ret")
        .rolling_std(window_size=252, min_samples=168)
        .over("symbol")
        .mul(math.sqrt(252.0))
        .alias("vol_252_ann"),
        pl.col("high")
        .rolling_max(window_size=252, min_samples=200)
        .over("symbol")
        .alias("_high_252"),
        pl.col("high")
        .rolling_max(window_size=63, min_samples=42)
        .over("symbol")
        .alias("_high_63"),
        pl.col("close").rolling_mean(50, min_samples=40).over("symbol").alias("_sma50"),
        (pl.col("_daily_ret") > 0.0)
        .cast(pl.Float64)
        .rolling_mean(window_size=63, min_samples=42)
        .over("symbol")
        .alias("positive_day_pct_63"),
        (pl.col("close") * pl.col("volume"))
        .rolling_mean(window_size=20, min_samples=15)
        .over("symbol")
        .alias("turnover_20d_inr"),
    ).with_columns(
        (pl.col("ret_63") - pl.col("prior_ret_63")).alias("momentum_acceleration_63"),
        (
            0.5 * pl.col("ret_126") / pl.col("vol_126_ann")
            + 0.5 * pl.col("ret_252") / pl.col("vol_252_ann")
        ).alias("risk_adjusted_momentum_6_12"),
        ((pl.col("close") / pl.col("_high_252") - 1.0) * 100.0).alias("distance_52w_high_pct"),
        ((pl.col("close") / pl.col("_high_63") - 1.0) * 100.0).alias("distance_13w_high_pct"),
        ((pl.col("close") / pl.col("_sma50") - 1.0) * 100.0).alias("sma50_extension_pct"),
        ((pl.col("open") / pl.col("_prev_close") - 1.0) * 100.0).alias("breakout_gap_pct"),
        ((pl.col("close") / pl.col("_prev_close") - 1.0) * 100.0).alias("breakout_day_return_pct"),
        ((pl.col("high") - pl.col("low")) / pl.col("_prev_close") * 100.0).alias("breakout_range_pct"),
        pl.when(pl.col("high") > pl.col("low"))
        .then((pl.col("close") - pl.col("low")) / (pl.col("high") - pl.col("low")))
        .otherwise(None)
        .alias("close_location_value"),
    )
    return features.select(
        pl.col("date").alias("signal_date"),
        "symbol",
        "ret_63",
        "ret_126",
        "ret_252",
        "prior_ret_63",
        "momentum_acceleration_63",
        "vol_63_ann",
        "vol_126_ann",
        "vol_252_ann",
        "risk_adjusted_momentum_6_12",
        "distance_52w_high_pct",
        "distance_13w_high_pct",
        "positive_day_pct_63",
        "sma50_extension_pct",
        "breakout_gap_pct",
        "breakout_day_return_pct",
        "breakout_range_pct",
        "close_location_value",
        "turnover_20d_inr",
    )


def _add_candidate_ranks(panel: pl.DataFrame) -> pl.DataFrame:
    count = pl.len().over("signal_date")

    def percentile(column: str, output: str) -> pl.Expr:
        return (
            pl.when(count > 1)
            .then(
                (pl.col(column).rank(method="average").over("signal_date") - 1.0)
                / (count - 1.0)
                * 100.0
            )
            .otherwise(100.0)
            .alias(output)
        )

    return panel.with_columns(
        percentile("risk_adjusted_momentum_6_12", "risk_adjusted_momentum_rank"),
        percentile("ret_63", "ret_63_candidate_rank"),
        percentile("distance_52w_high_pct", "high_52w_candidate_rank"),
    )


def _build_feature_cohorts(panel: pl.DataFrame) -> pl.DataFrame:
    rows: list[pl.DataFrame] = []
    era_expr = (
        pl.when(pl.col("signal_date").dt.year() <= 2017)
        .then(pl.lit("2010-2017"))
        .when(pl.col("signal_date").dt.year() <= 2022)
        .then(pl.lit("2018-2022"))
        .otherwise(pl.lit("2023+"))
        .alias("era")
    )
    enriched = panel.with_columns(era_expr)

    for feature in RESEARCH_FEATURES:
        if feature not in enriched.columns:
            continue
        valid = (
            enriched.filter(pl.col(feature).is_not_null() & pl.col(feature).is_finite())
            .with_columns(pl.col(feature).cast(pl.Float64).alias("_feature_value"))
        )
        if valid.height < 50:
            continue
        rank_pct = pl.col(feature).rank(method="average") / pl.len()
        bucketed = valid.with_columns(
            pl.when(rank_pct <= 0.20)
            .then(pl.lit("Q1"))
            .when(rank_pct <= 0.40)
            .then(pl.lit("Q2"))
            .when(rank_pct <= 0.60)
            .then(pl.lit("Q3"))
            .when(rank_pct <= 0.80)
            .then(pl.lit("Q4"))
            .otherwise(pl.lit("Q5"))
            .alias("quintile")
        )
        for era in ["all", "2010-2017", "2018-2022", "2023+"]:
            sample = bucketed if era == "all" else bucketed.filter(pl.col("era") == era)
            if sample.is_empty():
                continue
            rows.append(
                sample.group_by("quintile").agg(
                    pl.lit(feature).first().alias("feature"),
                    pl.lit(era).first().alias("era"),
                    pl.len().alias("signals"),
                    pl.col("_feature_value").min().alias("feature_min"),
                    pl.col("_feature_value").median().alias("feature_median"),
                    pl.col("_feature_value").max().alias("feature_max"),
                    pl.col("filled").mean().alias("fill_rate"),
                    pl.col("canonical_selected").mean().alias("canonical_selection_rate"),
                    pl.col("fwd_20d_r").mean().alias("avg_20d_r"),
                    pl.col("fwd_60d_r").mean().alias("avg_60d_r"),
                    pl.col("fwd_120d_r").mean().alias("avg_120d_r"),
                    pl.col("mfe_120d_r").mean().alias("avg_mfe_120d_r"),
                    pl.col("mae_120d_r").mean().alias("avg_mae_120d_r"),
                    pl.col("reached_3r").mean().alias("reached_3r_rate"),
                    pl.col("hit_stop_first").mean().alias("stop_first_rate"),
                )
            )

    if not rows:
        return pl.DataFrame()
    return pl.concat(rows, how="vertical").sort(["feature", "era", "quintile"])


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _signal_opportunity_funnel(
    panel: pl.DataFrame,
    trades: pl.DataFrame,
    settings: object,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, object], pl.DataFrame]:
    """Measure where executable breakout opportunities disappear.

    A ``major_signal_opportunity`` is a raw BREAKOUT that filled under the
    canonical D+1/pending-window contract, reached +5R before touching its
    original stop, and subsequently traded at least 50% above its fill within
    120 symbol sessions.  This is a hindsight label used only to measure recall;
    it is not an entry rule.
    """
    window = panel.filter(
        (pl.col("signal_date") >= start_date) & (pl.col("signal_date") <= end_date)
    )
    entered_keys = (
        trades.filter(pl.col("entry_date").is_not_null())
        .select("signal_date", "symbol")
        .unique()
        .with_columns(pl.lit(True).alias("portfolio_entered"))
    )
    window = window.join(entered_keys, on=["signal_date", "symbol"], how="left").with_columns(
        pl.col("portfolio_entered").fill_null(False)
    )

    scored = window.filter(pl.col("filled") & pl.col("mfe_pct").is_not_null())
    reached_3r_before_stop = pl.col("first_3r_bar").is_not_null() & (
        pl.col("first_stop_bar").is_null()
        | (pl.col("first_3r_bar") < pl.col("first_stop_bar"))
    )
    reached_5r_before_stop = pl.col("first_5r_bar").is_not_null() & (
        pl.col("first_stop_bar").is_null()
        | (pl.col("first_5r_bar") < pl.col("first_stop_bar"))
    )
    major = scored.filter((pl.col("mfe_pct") >= 50.0) & reached_5r_before_stop)
    three_r = scored.filter(reached_3r_before_stop)
    five_r = scored.filter(reached_5r_before_stop)

    def recalled(frame: pl.DataFrame, column: str) -> int:
        return frame.filter(pl.col(column)).height

    selected_major = recalled(major, "canonical_selected")
    entered_major = recalled(major, "portfolio_entered")
    selected_3r = recalled(three_r, "canonical_selected")
    entered_3r = recalled(three_r, "portfolio_entered")
    selected_5r = recalled(five_r, "canonical_selected")
    entered_5r = recalled(five_r, "portfolio_entered")

    rejection_counts: dict[str, int] = {}
    rejected_major = major.filter(~pl.col("canonical_selected"))
    checks = {
        "january_exclusion": pl.col("signal_date").dt.month().is_in(
            list(getattr(settings.backtest, "skip_months", None) or [])
        ),
        "rs_below_min": pl.col("rs_rank") < float(settings.backtest.rs_rank_min),
        "range10_above_max": pl.col("range10")
        > float(settings.backtest.signal_quality_range10_max),
        "voldry_above_max": pl.col("voldry")
        > float(settings.backtest.signal_quality_voldry_max),
        "contraction_count_above_max": pl.col("contraction_count")
        > int(settings.setup_vcp.contraction_max_count),
        "setup_streak_above_max": pl.col("setup_active_streak")
        > int(settings.backtest.signal_quality_setup_streak_max),
    }
    for label, expression in checks.items():
        rejection_counts[label] = rejected_major.filter(expression.fill_null(False)).height

    status_counts: dict[str, int] = {}
    if not trades.is_empty() and "status" in trades.columns:
        for row in trades.group_by("status").len().sort("len", descending=True).iter_rows(named=True):
            status_counts[str(row["status"])] = int(row["len"])

    summary: dict[str, object] = {
        "definition": (
            "raw D+1-fillable BREAKOUT reaching +5R before original stop and >=50% MFE "
            "within 120 symbol sessions"
        ),
        "raw_breakouts": window.height,
        "raw_fillable": scored.height,
        "canonical_selected": window.filter(pl.col("canonical_selected")).height,
        "portfolio_entered": window.filter(pl.col("portfolio_entered")).height,
        "major_signal_opportunities": major.height,
        "major_selected": selected_major,
        "major_entered": entered_major,
        "major_filter_recall": _rate(selected_major, major.height),
        "major_portfolio_capture": _rate(entered_major, major.height),
        "three_r_opportunities": three_r.height,
        "three_r_selected": selected_3r,
        "three_r_entered": entered_3r,
        "three_r_filter_recall": _rate(selected_3r, three_r.height),
        "three_r_portfolio_capture": _rate(entered_3r, three_r.height),
        "five_r_opportunities": five_r.height,
        "five_r_selected": selected_5r,
        "five_r_entered": entered_5r,
        "five_r_filter_recall": _rate(selected_5r, five_r.height),
        "five_r_portfolio_capture": _rate(entered_5r, five_r.height),
        "rejected_major_overlapping_reasons": rejection_counts,
        "canonical_trade_statuses": status_counts,
    }
    return summary, major.sort("mfe_pct", descending=True)


def _future_price_windows(
    ohlcv: pl.DataFrame,
    symbols: list[str],
    horizon: int = 120,
) -> pl.DataFrame:
    """Attach forward maximum and trading-session end dates without look-ahead use.

    These columns are ex-post labels for opportunity coverage only.  Sorting
    descending turns a trailing rolling window into a forward window; the
    result is sorted back before it leaves this helper.
    """
    ordered_desc = (
        ohlcv.filter(pl.col("symbol").is_in(symbols))
        .select("date", "symbol", "close", "high")
        .sort(["symbol", "date"], descending=[False, True])
    )
    return (
        ordered_desc.with_columns(
            pl.col("high")
            .rolling_max(window_size=horizon, min_samples=horizon)
            .over("symbol")
            .alias("future_max_high_120d"),
            pl.col("date").shift(horizon - 1).over("symbol").alias("window_end_120d"),
            pl.col("date").shift(59).over("symbol").alias("window_end_60d"),
            pl.col("date").shift(19).over("symbol").alias("window_end_20d"),
        )
        .sort(["symbol", "date"])
    )


def _first_signal_in_window(
    signal_dates: list[date],
    start_date: date,
    end_date: date | None,
) -> date | None:
    if end_date is None or not signal_dates:
        return None
    index = bisect_left(signal_dates, start_date)
    if index < len(signal_dates) and signal_dates[index] <= end_date:
        return signal_dates[index]
    return None


def _winner_episode_coverage(
    ohlcv: pl.DataFrame,
    diagnostics: pl.DataFrame,
    raw_signals: pl.DataFrame,
    canonical_signals: pl.DataFrame,
    start_date: date,
    end_date: date,
    *,
    winner_threshold: float = 0.50,
) -> tuple[dict[str, object], pl.DataFrame]:
    """Find non-overlapping liquid-universe winner episodes and signal recall.

    We sample the first eligible observation per symbol-month, label anchors
    whose price reaches +50% within 120 symbol sessions, then suppress
    overlapping 120-session windows for the same symbol.  This avoids counting
    every day of one large run as a separate winner.
    """
    eligible = diagnostics.filter(pl.col("eligible"))
    symbols = eligible["symbol"].unique().to_list()
    prices = _future_price_windows(ohlcv, symbols)
    anchors = (
        eligible.select(
            pl.col("diag_date").alias("date"),
            "symbol",
            "tt_pass",
            "setup_pass",
            "rs_rank",
        )
        .join(prices, on=["date", "symbol"], how="inner")
        .filter(
            (pl.col("date") >= start_date)
            & (pl.col("date") <= end_date)
            & pl.col("future_max_high_120d").is_not_null()
        )
        .with_columns(
            pl.col("date").dt.year().alias("year"),
            pl.col("date").dt.month().alias("month"),
            (pl.col("future_max_high_120d") / pl.col("close") - 1.0).alias(
                "future_max_return_120d"
            ),
        )
        .sort(["symbol", "date"])
        .group_by(["symbol", "year", "month"], maintain_order=True)
        .first()
        .sort(["symbol", "date"])
    )

    episode_rows: list[dict[str, object]] = []
    for symbol in anchors["symbol"].unique(maintain_order=True).to_list():
        last_window_end: date | None = None
        for row in anchors.filter(pl.col("symbol") == symbol).iter_rows(named=True):
            if float(row["future_max_return_120d"]) < winner_threshold:
                continue
            anchor_date = row["date"]
            if last_window_end is not None and anchor_date <= last_window_end:
                continue
            episode_rows.append(dict(row))
            last_window_end = row["window_end_120d"]

    if not episode_rows:
        return {
            "definition": "non-overlapping eligible symbol-month anchors reaching +50% within 120 sessions",
            "episodes": 0,
        }, pl.DataFrame()

    raw_dates: dict[str, list[date]] = {}
    canonical_dates: dict[str, list[date]] = {}
    for symbol in raw_signals["symbol"].unique().to_list():
        raw_dates[str(symbol)] = sorted(
            raw_signals.filter(pl.col("symbol") == symbol)["signal_date"].to_list()
        )
    for symbol in canonical_signals["symbol"].unique().to_list():
        canonical_dates[str(symbol)] = sorted(
            canonical_signals.filter(pl.col("symbol") == symbol)["signal_date"].to_list()
        )

    covered_rows: list[dict[str, object]] = []
    for row in episode_rows:
        symbol = str(row["symbol"])
        anchor_date = row["date"]
        enriched = dict(row)
        for days in (20, 60, 120):
            end_key = f"window_end_{days}d"
            raw_date = _first_signal_in_window(
                raw_dates.get(symbol, []), anchor_date, row.get(end_key)
            )
            canonical_date = _first_signal_in_window(
                canonical_dates.get(symbol, []), anchor_date, row.get(end_key)
            )
            enriched[f"raw_breakout_{days}d"] = raw_date is not None
            enriched[f"raw_breakout_date_{days}d"] = raw_date
            enriched[f"canonical_breakout_{days}d"] = canonical_date is not None
            enriched[f"canonical_breakout_date_{days}d"] = canonical_date
        covered_rows.append(enriched)

    episodes = pl.DataFrame(covered_rows, infer_schema_length=None).sort(
        "future_max_return_120d", descending=True
    )
    total = episodes.height
    summary = {
        "definition": "non-overlapping eligible symbol-month anchors reaching +50% within 120 sessions",
        "episodes": total,
        "raw_breakout_20d": int(episodes["raw_breakout_20d"].sum()),
        "raw_breakout_60d": int(episodes["raw_breakout_60d"].sum()),
        "raw_breakout_120d": int(episodes["raw_breakout_120d"].sum()),
        "canonical_breakout_20d": int(episodes["canonical_breakout_20d"].sum()),
        "canonical_breakout_60d": int(episodes["canonical_breakout_60d"].sum()),
        "canonical_breakout_120d": int(episodes["canonical_breakout_120d"].sum()),
        "raw_recall_120d": _rate(int(episodes["raw_breakout_120d"].sum()), total),
        "canonical_recall_120d": _rate(
            int(episodes["canonical_breakout_120d"].sum()), total
        ),
        "missed_completely": int((~episodes["raw_breakout_120d"]).sum()),
    }
    return summary, episodes


def _oracle_exit_bound(
    trades: pl.DataFrame,
    ohlcv: pl.DataFrame,
    start_date: date,
    end_date: date,
    initial_capital: float,
    *,
    horizon: int = 120,
) -> tuple[dict[str, object], pl.DataFrame]:
    """Compute a deliberately impossible same-entry hindsight exit bound.

    Position sizes and entries remain exactly those produced by the canonical
    engine.  For each entered trade, the oracle chooses the best high available
    before the original hard stop is touched (stop-day collisions are treated
    conservatively) or 120 symbol sessions elapse.  It ignores trailing exits,
    overlapping capital, and reinvestment, so it is a diagnostic bound rather
    than another backtest.
    """
    entered = trades.filter(
        pl.col("entry_date").is_not_null()
        & (pl.col("entry_date") >= start_date)
        & (pl.col("entry_date") <= end_date)
    )
    price_map = {
        str(symbol): ohlcv.filter(pl.col("symbol") == symbol).sort("date")
        for symbol in entered["symbol"].unique().to_list()
    }
    rows: list[dict[str, object]] = []
    for trade in entered.iter_rows(named=True):
        symbol = str(trade["symbol"])
        entry_date = trade["entry_date"]
        entry_price = float(trade["entry_price"])
        stop = float(trade["stop"])
        risk = float(trade["initial_risk_price"] or 0.0)
        shares = float(trade["shares"] or 0.0)
        future = price_map[symbol].filter(pl.col("date") > entry_date).head(horizon)
        feasible_peak = entry_price
        feasible_peak_date: date | None = entry_date
        unrestricted_peak = entry_price
        stop_date: date | None = None
        for bar in future.iter_rows(named=True):
            high = float(bar["high"])
            low = float(bar["low"])
            if high > unrestricted_peak:
                unrestricted_peak = high
            if low <= stop:
                stop_date = bar["date"]
                break
            if high > feasible_peak:
                feasible_peak = high
                feasible_peak_date = bar["date"]

        feasible_r = (feasible_peak - entry_price) / risk if risk > 0.0 else None
        unrestricted_r = (unrestricted_peak - entry_price) / risk if risk > 0.0 else None
        oracle_pnl = shares * max(0.0, feasible_peak - entry_price)
        rows.append(
            {
                "symbol": symbol,
                "signal_date": trade["signal_date"],
                "entry_date": entry_date,
                "actual_exit_date": trade["exit_date"],
                "actual_status": trade["status"],
                "entry_price": entry_price,
                "original_stop": stop,
                "shares": shares,
                "actual_pnl": float(trade["pnl"] or 0.0),
                "actual_r": trade["r_multiple"],
                "feasible_peak": feasible_peak,
                "feasible_peak_date": feasible_peak_date,
                "first_original_stop_date": stop_date,
                "feasible_oracle_r": feasible_r,
                "unrestricted_oracle_r": unrestricted_r,
                "feasible_oracle_pnl": oracle_pnl,
            }
        )

    oracle = pl.DataFrame(rows, infer_schema_length=None)
    if oracle.is_empty():
        return {"trades": 0}, oracle
    actual_pnl = float(oracle["actual_pnl"].sum())
    oracle_pnl = float(oracle["feasible_oracle_pnl"].sum())
    years = max((end_date - start_date).days / 365.25, 1e-9)
    oracle_terminal = initial_capital + oracle_pnl
    oracle_equivalent_cagr = (
        (oracle_terminal / initial_capital) ** (1.0 / years) - 1.0
        if oracle_terminal > 0.0
        else -1.0
    )
    summary = {
        "definition": "same canonical entries/sizes; best pre-original-stop high within 120 sessions",
        "important_limitations": [
            "hindsight exit, therefore not tradable",
            "holds position sizes fixed and ignores reinvestment",
            "ignores overlapping-capital and opportunity-cost changes caused by later exits",
            "uses the original hard stop but ignores tighter trailing exits",
        ],
        "trades": oracle.height,
        "actual_trade_pnl": actual_pnl,
        "feasible_oracle_trade_pnl": oracle_pnl,
        "actual_to_oracle_pnl_ratio": actual_pnl / oracle_pnl if oracle_pnl else None,
        "oracle_equivalent_terminal_equity": oracle_terminal,
        "oracle_equivalent_cagr": oracle_equivalent_cagr,
        "median_actual_r": float(oracle["actual_r"].median()),
        "median_feasible_oracle_r": float(oracle["feasible_oracle_r"].median()),
        "mean_actual_r": float(oracle["actual_r"].mean()),
        "mean_feasible_oracle_r": float(oracle["feasible_oracle_r"].mean()),
        "trades_with_3r_feasible": oracle.filter(pl.col("feasible_oracle_r") >= 3.0).height,
        "trades_with_5r_feasible": oracle.filter(pl.col("feasible_oracle_r") >= 5.0).height,
    }
    return summary, oracle.sort("feasible_oracle_pnl", descending=True)


def _pct(value: object) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100.0:.1f}%"


def _write_opportunity_report(
    output_dir: Path,
    summary: dict[str, object],
    major_signals: pl.DataFrame,
    winner_episodes: pl.DataFrame,
    oracle_trades: pl.DataFrame,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "opportunity_ceiling.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    major_signals.write_csv(output_dir / "major_signal_opportunities.csv")
    winner_episodes.write_csv(output_dir / "winner_episodes.csv")
    if not winner_episodes.is_empty():
        winner_episodes.filter(~pl.col("raw_breakout_120d")).write_csv(
            output_dir / "missed_winner_episodes.csv"
        )
    oracle_trades.write_csv(output_dir / "oracle_exit_trades.csv")

    funnel = summary["signal_funnel"]
    coverage = summary["winner_episode_coverage"]
    oracle = summary["oracle_exit_bound"]
    conclusion = summary["conclusion"]
    lines = [
        "# Opportunity-Ceiling Diagnosis",
        "",
        f"**Window:** {summary['window']['start']} to {summary['window']['end']}",
        "",
        "This is a diagnostic, not a strategy backtest. Forward maxima and oracle exits use",
        "hindsight only to locate the bottleneck; they must never be treated as tradable results.",
        "",
        "## Result",
        "",
        str(conclusion["headline"]),
        "",
        str(conclusion["interpretation"]),
        "",
        "## Signal funnel",
        "",
        f"- Raw breakouts: {funnel['raw_breakouts']}",
        f"- D+1-fillable raw breakouts: {funnel['raw_fillable']}",
        f"- Canonical-selected breakouts: {funnel['canonical_selected']}",
        f"- Portfolio entries: {funnel['portfolio_entered']}",
        f"- Major signal opportunities (>=50% 120-session MFE): {funnel['major_signal_opportunities']}",
        f"- Filter recall of major signal opportunities: {_pct(funnel['major_filter_recall'])}",
        f"- Final portfolio capture of major signal opportunities: {_pct(funnel['major_portfolio_capture'])}",
        "",
        "## Universe winner coverage",
        "",
        f"- Non-overlapping eligible +50% winner episodes: {coverage['episodes']}",
        f"- Any raw breakout within 120 sessions: {coverage['raw_breakout_120d']} ({_pct(coverage['raw_recall_120d'])})",
        f"- Canonical breakout within 120 sessions: {coverage['canonical_breakout_120d']} ({_pct(coverage['canonical_recall_120d'])})",
        f"- Winner episodes missed by the breakout generator entirely: {coverage['missed_completely']}",
        "",
        "## Same-entry hindsight exit bound",
        "",
        f"- Actual trade P&L: INR {oracle['actual_trade_pnl']:,.0f}",
        f"- Best pre-original-stop 120-session P&L, fixed historical sizes: INR {oracle['feasible_oracle_trade_pnl']:,.0f}",
        f"- Actual/oracle P&L capture: {_pct(oracle['actual_to_oracle_pnl_ratio'])}",
        f"- Non-compounded oracle-equivalent CAGR: {_pct(oracle['oracle_equivalent_cagr'])}",
        "",
        "## Decision",
        "",
        f"- Primary bottleneck: **{conclusion['primary_bottleneck']}**",
        f"- Can exit work alone credibly justify a 30% target? **{conclusion['exit_only_can_justify_30pct']}**",
        f"- Recommended next experiment: {conclusion['recommended_next_experiment']}",
        "",
        "## Caveats",
        "",
        "- Major winners are ex-post labels, sampled from the first eligible date per symbol-month.",
        "- Overlapping 120-session winner windows for the same symbol are suppressed.",
        "- Oracle exits know the future, hold historical position sizes fixed, and ignore the",
        "  portfolio schedule that different exit dates would create.",
        "- Filter recall is descriptive, not permission to loosen filters; rejected winners must",
        "  be weighed against the additional losers each filter removes.",
        "- Therefore the oracle number diagnoses latent opportunity; it is not an expected return.",
    ]
    (output_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_canonical_strategy_only(
    start_date: date,
    end_date: date,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    bundle, adjusted_signals, ohlcv = diag.load_inputs(start_date, end_date)
    settings = bundle["settings"]
    ref = DATA_DIR / "reference"
    full_map = ref / "full_sector_map.csv"
    fallback = ref / "nifty500_constituents.csv"
    sector_map = load_sector_map(full_map if full_map.exists() else fallback)
    filtered = diag.filter_signals(
        adjusted_signals,
        settings=settings,
        diagnostics=bundle["diagnostics"],
        sector_map=sector_map,
        rs_rank_min=settings.backtest.rs_rank_min,
        rs_rank_max=settings.backtest.rs_rank_max,
        ranking_mode=settings.backtest.signal_ranking_mode,
        max_signals_per_day=settings.backtest.max_signals_per_day,
        quality_profile=settings.backtest.signal_quality_profile,
        quality_overrides=diag.resolve_signal_quality_overrides(settings),
    )

    portfolio = dict(bundle["portfolio"])
    portfolio["cash_yield_apy"] = 0.0
    benchmark = (
        ohlcv.filter(pl.col("symbol") == settings.market.benchmark_symbol)
        .select("date", pl.col("close").alias("tri"))
        .sort("date")
    )
    regime = diag.compute_regime_inputs(settings, start_date, end_date, benchmark)
    entry_gate: dict[date, bool] = {}
    entry_size_mult: dict[date, float] = {}
    if settings.backtest.use_regime_entry_controls:
        entry_gate, entry_size_mult = diag.build_entry_controls(settings, regime)

    result = diag.simulate_portfolio_python(
        filtered,
        ohlcv,
        bundle["params"],
        portfolio,
        sector_map,
        exit_config=bundle["exit_config"] if settings.backtest.use_extended_exit_model else None,
        entry_gate=entry_gate,
        entry_size_mult=entry_size_mult,
        breakeven_activation_r=settings.exit_strategy.breakeven_activation_r,
        time_stop_days=settings.exit_strategy.time_stop_days,
        partial_profit_r=settings.exit_strategy.partial_profit_r,
        trail_pct_bull=settings.exit_strategy.trail_pct_bull,
        trail_pct_bear=settings.exit_strategy.trail_pct_bear,
        trail_widen_r=getattr(settings.exit_strategy, "trail_widen_r", 3.0),
    )
    return result["trades"], result["daily_equity"]


def main() -> None:
    conn = duckdb.connect(str(DB_PATH), read_only=True)

    raw_signals = conn.execute(
        "SELECT * FROM signals WHERE signal_type = 'BREAKOUT' ORDER BY signal_date"
    ).pl()
    diagnostics = conn.execute("SELECT * FROM daily_diagnostics ORDER BY diag_date, symbol").pl()
    conn.close()

    settings = load_settings()
    signals = diag.filter_signals(
        raw_signals,
        settings=settings,
        diagnostics=diagnostics,
        sector_map=None,
        rs_rank_min=settings.backtest.rs_rank_min,
        rs_rank_max=settings.backtest.rs_rank_max,
        ranking_mode=settings.backtest.signal_ranking_mode,
        max_signals_per_day=settings.backtest.max_signals_per_day,
        quality_profile=settings.backtest.signal_quality_profile,
        quality_overrides=diag.resolve_signal_quality_overrides(settings),
    )
    print(f"Canonical entry filters: {signals.height}/{raw_signals.height} raw breakouts retained")

    parquet = ParquetOhlcvStore(settings.runtime.data_dir / settings.data.panel_subdir)
    symbols = raw_signals["symbol"].unique().to_list()
    symbols.append(settings.market.benchmark_symbol)
    all_ohlcv = parquet.read_frame(
        columns=["date", "symbol", "open", "high", "low", "close", "volume"]
    ).sort(["symbol", "date"])
    ohlcv = all_ohlcv.filter(pl.col("symbol").is_in(symbols))

    result = compute_scorecard(
        signals,
        ohlcv,
        diagnostics,
        forward_days=FORWARD_DAYS,
        pending_days=settings.backtest.pending_days,
    )

    print("Building non-aggregated signal feature panel ...")
    metrics = compute_signal_metrics(
        raw_signals,
        ohlcv,
        diagnostics,
        forward_days=FORWARD_DAYS,
        pending_days=settings.backtest.pending_days,
    )
    price_features = _build_price_features(
        ohlcv.filter(pl.col("symbol") != settings.market.benchmark_symbol)
    )
    selected_keys = signals.select("signal_date", "symbol").with_columns(
        pl.lit(True).alias("canonical_selected")
    )
    panel = (
        metrics.join(price_features, on=["signal_date", "symbol"], how="left")
        .join(selected_keys, on=["signal_date", "symbol"], how="left")
        .with_columns(
            pl.col("canonical_selected").fill_null(False),
            pl.col("signal_date").dt.year().alias("signal_year"),
            pl.col("signal_date").dt.month().alias("signal_month"),
        )
    )

    benchmark_curve = (
        ohlcv.filter(pl.col("symbol") == settings.market.benchmark_symbol)
        .select("date", pl.col("close").alias("tri"))
        .sort("date")
    )
    panel_start = panel["signal_date"].min()
    panel_end = panel["signal_date"].max()
    regime = diag.compute_regime_inputs(settings, panel_start, panel_end, benchmark_curve)
    panel = panel.join(
        regime.rename({"date": "signal_date"}),
        on="signal_date",
        how="left",
    )
    panel = _add_candidate_ranks(panel)

    print("Running finalized canonical strategy-only simulation ...")
    canonical_end = min(date(2026, 3, 17), panel_end)
    trades, equity = _run_canonical_strategy_only(date(2010, 1, 4), canonical_end)
    trade_columns = [
        "symbol",
        "signal_date",
        "status",
        "entry_date",
        "entry_price",
        "exit_date",
        "exit_price",
        "holding_days",
        "return_pct",
        "r_multiple",
        "mae_price",
        "mfe_price",
        "initial_risk_price",
        "mae_r",
        "mfe_r",
        "giveback_r",
        "peak_gain_capture_pct",
        "pnl",
        "sector",
    ]
    available_trade_columns = [column for column in trade_columns if column in trades.columns]
    trade_outcomes = trades.select(available_trade_columns).rename(
        {
            column: f"canonical_{column}"
            for column in available_trade_columns
            if column not in {"symbol", "signal_date"}
        }
    )
    panel = panel.join(trade_outcomes, on=["signal_date", "symbol"], how="left")

    cohorts = _build_feature_cohorts(panel)
    correlation_columns = [
        column
        for column in [
            *RESEARCH_FEATURES,
            "risk_adjusted_momentum_rank",
            "ret_63_candidate_rank",
            "high_52w_candidate_rank",
            "fwd_20d_r",
            "fwd_60d_r",
            "fwd_120d_r",
            "mfe_120d_r",
            "mae_120d_r",
            "canonical_r_multiple",
            "canonical_return_pct",
        ]
        if column in panel.columns and panel[column].dtype.is_numeric()
    ]
    correlations = panel.select(correlation_columns).corr()
    correlations.insert_column(0, pl.Series("feature", correlation_columns))

    overall = result.get("overall", {})
    print("Signal Quality Research")
    print("=======================")
    print(f"Signals: {result.get('signals_with_fwd_data', 0)}/{result.get('total_signals', 0)} with forward data")
    print(
        "Overall:"
        f" avg MFE={float(overall.get('avg_mfe_r') or 0.0):.2f}R"
        f", 2R={float(overall.get('reached_2r_pct') or 0.0):.1f}%"
        f", 3R={float(overall.get('reached_3r_pct') or 0.0):.1f}%"
        f", 5R={float(overall.get('reached_5r_pct') or 0.0):.1f}%"
        f", stop-first={float(overall.get('stop_hit_first_pct') or 0.0):.1f}%"
    )

    for key, label in SECTIONS[1:]:
        rows = result.get(key) or []
        rows = [row for row in rows if int(row.get("count", 0)) >= 20]
        if not rows:
            continue

        ranked = sorted(rows, key=_sort_key, reverse=True)
        print(f"\nTop {label} cohorts")
        for row in ranked[:4]:
            _print_row(_bucket_label(row), row)

        print(f"Worst {label} cohorts")
        for row in list(reversed(ranked[-3:])):
            _print_row(_bucket_label(row), row)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    panel.sort(["signal_date", "symbol"]).write_parquet(SIGNAL_PANEL_PATH, compression="zstd")
    if not cohorts.is_empty():
        cohorts.write_csv(COHORT_PATH)
    correlations.write_csv(CORRELATION_PATH)
    trades.write_parquet(TRADE_PATH, compression="zstd")
    equity.write_parquet(EQUITY_PATH, compression="zstd")

    eq = equity.filter(
        (pl.col("date") >= date(2010, 1, 4)) & (pl.col("date") <= canonical_end)
    ).sort("date")
    strategy_only_metrics: dict[str, float | int] = {}
    if eq.height:
        years = max((eq["date"][-1] - eq["date"][0]).days / 365.25, 1e-9)
        start_equity = float(eq["equity"][0])
        end_equity = float(eq["equity"][-1])
        cagr = (end_equity / start_equity) ** (1.0 / years) - 1.0
        drawdown = eq["equity"] / eq["equity"].cum_max() - 1.0
        max_drawdown = abs(float(drawdown.min()))
        strategy_only_metrics = {
            "cagr": cagr,
            "max_drawdown": max_drawdown,
            "calmar": cagr / max_drawdown if max_drawdown > 0.0 else 0.0,
            "filled_trades": trades.filter(pl.col("entry_date").is_not_null()).height,
        }

    result["canonical_strategy_only_corrected"] = strategy_only_metrics
    print("Running opportunity-ceiling diagnosis ...")
    opportunity_start = date(2010, 1, 4)
    opportunity_end = canonical_end
    signal_funnel, major_signals = _signal_opportunity_funnel(
        panel,
        trades,
        settings,
        opportunity_start,
        opportunity_end,
    )
    winner_coverage, winner_episodes = _winner_episode_coverage(
        all_ohlcv,
        diagnostics,
        raw_signals.filter(
            (pl.col("signal_date") >= opportunity_start)
            & (pl.col("signal_date") <= opportunity_end)
        ),
        signals.filter(
            (pl.col("signal_date") >= opportunity_start)
            & (pl.col("signal_date") <= opportunity_end)
        ),
        opportunity_start,
        opportunity_end,
    )
    oracle_bound, oracle_trades = _oracle_exit_bound(
        trades,
        all_ohlcv,
        opportunity_start,
        opportunity_end,
        float(settings.portfolio.initial_capital),
    )

    raw_recall = float(winner_coverage.get("raw_recall_120d") or 0.0)
    filter_recall = float(signal_funnel.get("major_filter_recall") or 0.0)
    oracle_cagr = float(oracle_bound.get("oracle_equivalent_cagr") or 0.0)
    pnl_capture = float(oracle_bound.get("actual_to_oracle_pnl_ratio") or 0.0)
    if raw_recall < 0.50:
        primary_bottleneck = "entry signal supply"
        interpretation = (
            "The breakout generator misses most major eligible-universe runs; exit tuning cannot "
            "monetize opportunities that never become entries."
        )
    elif filter_recall < 0.60:
        primary_bottleneck = "entry-filter recall"
        interpretation = (
            "The raw breakout stream sees many major runs, but the canonical filters discard too "
            "many of them before portfolio construction."
        )
    elif oracle_cagr >= 0.30 and pnl_capture < 0.50:
        primary_bottleneck = "exit capture"
        interpretation = (
            "The entered signal set contains substantial latent upside, but the canonical exits "
            "capture a minority of the impossible hindsight bound."
        )
    else:
        primary_bottleneck = "combined signal scarcity and payoff density"
        interpretation = (
            "Neither a single filter nor ordinary exit giveback explains the gap; the strategy "
            "needs additional independent opportunities rather than another local parameter tweak."
        )

    if oracle_cagr < 0.30:
        exit_only_answer = "No; even the fixed-size hindsight bound remains below 30% CAGR."
    else:
        exit_only_answer = (
            "No as a credible strategy; only an untradeable hindsight exit bound exceeds 30%."
        )
    opportunity_summary: dict[str, object] = {
        "window": {
            "start": opportunity_start.isoformat(),
            "end": opportunity_end.isoformat(),
        },
        "data_source": settings.data.source,
        "signal_funnel": signal_funnel,
        "winner_episode_coverage": winner_coverage,
        "oracle_exit_bound": oracle_bound,
        "conclusion": {
            "headline": (
                f"Primary bottleneck: {primary_bottleneck}. "
                f"Raw winner-episode recall={raw_recall:.1%}; "
                f"major-signal filter recall={filter_recall:.1%}; "
                f"actual/oracle P&L capture={pnl_capture:.1%}."
            ),
            "primary_bottleneck": primary_bottleneck,
            "interpretation": interpretation,
            "exit_only_can_justify_30pct": exit_only_answer,
            "recommended_next_experiment": (
                "the pre-registered pullback-continuation stream, behind a disabled config flag, "
                "because it directly targets missing entry supply"
            ),
        },
    }
    opportunity_dir = (
        OPPORTUNITY_ROOT
        / f"{opportunity_start.isoformat()}_{opportunity_end.isoformat()}"
    )
    _write_opportunity_report(
        opportunity_dir,
        opportunity_summary,
        major_signals,
        winner_episodes,
        oracle_trades,
    )
    result["opportunity_ceiling"] = opportunity_summary
    result["analysis_outputs"] = {
        "signal_feature_panel": str(SIGNAL_PANEL_PATH),
        "feature_cohorts": str(COHORT_PATH),
        "feature_correlations": str(CORRELATION_PATH),
        "canonical_trade_outcomes": str(TRADE_PATH),
        "canonical_strategy_only_equity": str(EQUITY_PATH),
        "opportunity_ceiling_report": str(opportunity_dir / "REPORT.md"),
        "panel_rows": panel.height,
        "panel_columns": len(panel.columns),
    }
    OUTPUT_PATH.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(
        f"\nSaved {panel.height:,} signals x {len(panel.columns)} columns to {SIGNAL_PANEL_PATH}"
    )
    print(f"Saved cohorts to {COHORT_PATH}")
    print(f"Saved correlations to {CORRELATION_PATH}")
    print(f"Saved canonical outcomes to {TRADE_PATH}")
    print(f"Saved opportunity ceiling to {opportunity_dir / 'REPORT.md'}")
    print(f"Saved summary to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
