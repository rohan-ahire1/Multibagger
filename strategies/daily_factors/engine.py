from __future__ import annotations

import io
import json
import math
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OHLCV = ROOT / "data" / "yf_adjusted" / "ohlcv_all.parquet"
DEFAULT_SNAPSHOTS = ROOT / "data" / "raw" / "universe_snapshots"
DEFAULT_OUTPUT = ROOT / "strategies" / "daily_factors" / "output"
DEFAULT_SEPA_SUMMARY = (
    ROOT
    / "reports"
    / "portfolio_diagnostics"
    / "2010-01-04_2026-03-17"
    / "summary.json"
)
DEFAULT_NIFTY500_TRI = ROOT / "data" / "reference" / "nifty500_tri_2010-01-04_2026-03-17.csv"


@dataclass(frozen=True)
class ResearchConfig:
    start: date = date(2010, 1, 4)
    end: date = date(2026, 3, 17)
    initial_capital: float = 1_000_000.0
    slippage_bps: float = 10.0
    universe_size: int = 200
    alpha_universe_size: int = 150
    momentum_holdings: int = 20
    momentum_selection_pct: float = 0.20
    high_52w_holdings: int = 20
    alpha_holdings: int = 30
    combined_holdings: int = 30
    alpha_weight_cap: float = 0.05
    liquidity_lookback: int = 63
    benchmark_symbol: str = "NIFTYBEES"


@dataclass
class Holding:
    shares: int
    last_price: float


@dataclass
class PortfolioResult:
    name: str
    daily_equity: pl.DataFrame
    orders: pl.DataFrame
    metrics: dict[str, Any]


def _month_end_signal_dates(calendar: list[date], start: date, end: date) -> list[date]:
    """Use actual completed calendar months, avoiding a truncated final month."""
    by_month: dict[tuple[int, int], list[date]] = defaultdict(list)
    for value in calendar:
        by_month[(value.year, value.month)].append(value)
    actual_ends = {key: max(values) for key, values in by_month.items()}
    return [
        value
        for key, value in sorted(actual_ends.items())
        if start <= value <= end and value == actual_ends[key]
    ]


def _snapshot_date(path: Path) -> date:
    return date.fromisoformat(path.stem)


def _read_eq_snapshot(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not names:
            return set()
        raw = archive.read(names[0])
    frame = pl.read_csv(io.BytesIO(raw), infer_schema_length=100, ignore_errors=True)
    if {"SYMBOL", "SERIES"}.issubset(frame.columns):
        symbol_col, series_col = "SYMBOL", "SERIES"
    elif {"TckrSymb", "SctySrs"}.issubset(frame.columns):
        symbol_col, series_col = "TckrSymb", "SctySrs"
    else:
        raise ValueError(f"Unsupported NSE snapshot schema: {path.name}")
    return set(
        frame.filter(pl.col(series_col).cast(pl.String).str.strip_chars() == "EQ")
        .select(pl.col(symbol_col).cast(pl.String).str.strip_chars())
        .drop_nulls()
        .to_series()
        .to_list()
    )


def load_point_in_time_membership(
    signal_dates: list[date], snapshot_dir: Path = DEFAULT_SNAPSHOTS
) -> tuple[dict[date, set[str]], dict[date, date]]:
    """Map every signal date to the latest NSE EQ snapshot known on that date."""
    snapshot_paths = sorted(snapshot_dir.glob("*.zip"), key=_snapshot_date)
    if not snapshot_paths:
        raise FileNotFoundError(f"No point-in-time universe snapshots found under {snapshot_dir}")
    available = [(_snapshot_date(path), path) for path in snapshot_paths]
    membership: dict[date, set[str]] = {}
    source_dates: dict[date, date] = {}
    cursor = 0
    latest: tuple[date, Path] | None = None
    cache: dict[Path, set[str]] = {}
    for signal_date in sorted(signal_dates):
        while cursor < len(available) and available[cursor][0] <= signal_date:
            latest = available[cursor]
            cursor += 1
        if latest is None:
            raise RuntimeError(f"No universe snapshot exists on or before {signal_date}")
        snap_date, path = latest
        if path not in cache:
            cache[path] = _read_eq_snapshot(path)
        membership[signal_date] = cache[path]
        source_dates[signal_date] = snap_date
    return membership, source_dates


def load_and_build_features(
    config: ResearchConfig, ohlcv_path: Path = DEFAULT_OHLCV
) -> tuple[pl.DataFrame, list[date], dict[date, date]]:
    """Build the feature panel.

    ``ohlcv_path`` defaults to the legacy yfinance panel, which is what the
    recorded results in ``output/`` were produced from. The strategy lab passes
    the configured survivorship-free panel instead; features and execution must
    come from the same panel or targeted symbols go missing at fill time.
    """
    columns = ["date", "symbol", "open", "close", "turnover"]
    panel = (
        pl.scan_parquet(ohlcv_path)
        .select(columns)
        .filter(pl.col("date") <= config.end)
        .collect()
        .sort(["symbol", "date"])
    )
    # Use broad-coverage market dates rather than blindly trusting one ETF's
    # calendar. Yahoo contains a handful of sparse special sessions (notably
    # 2015-02-28) with too few stocks to support a cross-sectional rebalance.
    full_benchmark_calendar = (
        pl.scan_parquet(ohlcv_path)
        .group_by("date")
        .agg(pl.col("symbol").n_unique().alias("symbols"))
        .filter(pl.col("symbols") >= 100)
        .select("date")
        .collect()
        .sort("date")
        .to_series()
        .to_list()
    )
    benchmark = [value for value in full_benchmark_calendar if value <= config.end]
    if not full_benchmark_calendar:
        raise RuntimeError(f"Benchmark calendar {config.benchmark_symbol} is missing")
    signal_dates = _month_end_signal_dates(full_benchmark_calendar, config.start, config.end)
    membership, source_dates = load_point_in_time_membership(signal_dates)

    market_returns = (
        panel.filter(pl.col("symbol") == config.benchmark_symbol)
        .select(
            "date",
            (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("market_return"),
        )
    )
    base = panel.join(market_returns, on="date", how="left").with_columns(
        (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("stock_return"),
        pl.col("close").shift(21).over("symbol").alias("close_lag21"),
        pl.col("close").shift(252).over("symbol").alias("close_lag252"),
        pl.col("close").rolling_max(252).over("symbol").alias("high_close_252"),
        pl.col("close").rolling_mean(200).over("symbol").alias("sma200"),
        pl.col("turnover").rolling_mean(config.liquidity_lookback).over("symbol").alias("adv63"),
    )
    base = base.with_columns(
        (pl.col("close_lag21") / pl.col("close_lag252") - 1.0).alias("momentum_12_1"),
        (pl.col("close") / pl.col("high_close_252")).alias("high_52w_score"),
        pl.col("stock_return").rolling_mean(252, min_samples=200).over("symbol").alias("stock_mean"),
        pl.col("market_return").rolling_mean(252, min_samples=200).over("symbol").alias("market_mean"),
        (pl.col("stock_return") * pl.col("market_return"))
        .rolling_mean(252, min_samples=200)
        .over("symbol")
        .alias("cross_mean"),
        (pl.col("market_return") * pl.col("market_return"))
        .rolling_mean(252, min_samples=200)
        .over("symbol")
        .alias("market_square_mean"),
        (
            pl.col("stock_return").rolling_std(252, min_samples=200).over("symbol")
            * math.sqrt(252.0)
        ).alias("volatility"),
    )
    base = base.with_columns(
        (
            (pl.col("cross_mean") - pl.col("stock_mean") * pl.col("market_mean"))
            / (pl.col("market_square_mean") - pl.col("market_mean") ** 2)
        ).alias("beta")
    ).with_columns(
        ((pl.col("stock_mean") - pl.col("beta") * pl.col("market_mean")) * 252.0).alias("jensen_alpha")
    )
    feature_rows = (
        base.filter(pl.col("date").is_in(signal_dates))
        .filter(pl.col("symbol") != config.benchmark_symbol)
        .select(
            "date",
            "symbol",
            "close",
            "adv63",
            "momentum_12_1",
            "high_52w_score",
            "sma200",
            "jensen_alpha",
            "volatility",
        )
        .collect() if isinstance(base, pl.LazyFrame) else base.filter(pl.col("date").is_in(signal_dates))
        .filter(pl.col("symbol") != config.benchmark_symbol)
        .select(
            "date", "symbol", "close", "adv63", "momentum_12_1", "high_52w_score",
            "sma200", "jensen_alpha", "volatility"
        )
    )
    return feature_rows.sort(["date", "symbol"]), benchmark, source_dates


def build_pit_momentum_targets(
    panel: pl.DataFrame,
    *,
    start: date,
    end: date,
    universe_size: int = 500,
    selection_pct: float = 0.20,
    liquidity_lookback: int = 60,
    min_history_sessions: int = 200,
    rebalance_shift: int = 0,
    min_market_symbols: int = 100,
    exclude_symbols: set[str] | None = None,
) -> tuple[dict[date, dict[str, float]], list[date], pl.DataFrame]:
    """Build monthly 12-1 targets from strictly point-in-time observations.

    The feature row used at signal date ``T`` is the latest observation with
    ``date < T``.  Liquidity is the trailing median turnover over the previous
    ``liquidity_lookback`` observations; momentum is the return from the
    observation approximately 12 months before that row to the observation
    approximately one month before it.  Thus neither the signal-date close nor
    any future row can enter eligibility, ranking, or weights.

    ``rebalance_shift`` is expressed in market sessions relative to each
    calendar month-end (negative/positive values are used only for sensitivity
    analysis).  The returned audit frame retains the observation date used for
    every signal.
    """
    if universe_size <= 0 or liquidity_lookback <= 0 or min_history_sessions <= 0:
        raise ValueError("universe_size, liquidity_lookback and min_history_sessions must be positive")
    if not 0.0 < selection_pct <= 1.0:
        raise ValueError("selection_pct must be in (0, 1]")
    if panel.is_empty():
        empty = pl.DataFrame(
            schema={
                "signal_date": pl.Date,
                "observation_date": pl.Date,
                "symbol": pl.String,
                "adv": pl.Float64,
                "momentum": pl.Float64,
                "weight": pl.Float64,
            }
        )
        return {}, [], empty

    required = {"date", "symbol", "close", "volume"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"panel missing momentum columns: {sorted(missing)}")
    exclusions = exclude_symbols or set()
    if "turnover" not in panel.columns:
        panel = panel.with_columns((pl.col("close") * pl.col("volume")).alias("turnover"))
    clean = (
        panel.select("date", "symbol", "close", "volume", "turnover")
        .filter(pl.col("date") <= end)
        .filter(~pl.col("symbol").is_in(sorted(exclusions)))
        .sort(["symbol", "date"])
    )
    if clean.is_empty():
        return {}, [], pl.DataFrame()

    market_sessions = (
        clean.group_by("date")
        .agg(pl.col("symbol").n_unique().alias("symbol_count"))
        .filter(pl.col("symbol_count") >= min_market_symbols)
        .select("date")
        .sort("date")
        .to_series()
        .to_list()
    )
    month_ends = _month_end_signal_dates(market_sessions, start, end)
    if rebalance_shift:
        session_index = {session: index for index, session in enumerate(market_sessions)}
        shifted: list[date] = []
        for month_end in month_ends:
            index = session_index[month_end] + rebalance_shift
            if 0 <= index < len(market_sessions):
                shifted.append(market_sessions[index])
        signal_dates = sorted(set(shifted))
    else:
        signal_dates = month_ends
    if not signal_dates:
        return {}, market_sessions, pl.DataFrame()

    # All rolling/lagged columns are calculated in symbol-time order.  The
    # one-row shift on liquidity/positive-volume deliberately excludes the
    # current observation, even when a symbol happens to trade on T.
    feature_panel = clean.with_columns(
        [
            (pl.col("date").cum_count().over("symbol") - 1).cast(pl.Int64).alias("history_before"),
            (
                ((pl.col("turnover") > 0) & (pl.col("volume") > 0))
                .cast(pl.Int8)
                .shift(1)
                .rolling_sum(liquidity_lookback, min_samples=liquidity_lookback)
                .over("symbol")
            ).alias("positive_count"),
            pl.col("turnover")
            .shift(1)
            .rolling_median(liquidity_lookback, min_samples=liquidity_lookback)
            .over("symbol")
            .alias("adv"),
            pl.col("close").shift(21).over("symbol").alias("close_skip_month"),
            pl.col("close").shift(252).over("symbol").alias("close_skip_year"),
        ]
    ).with_columns(
        (pl.col("close_skip_month") / pl.col("close_skip_year") - 1.0).alias("momentum")
    )

    # A small signal-date x symbol grid lets join_asof choose the latest row
    # strictly before T without repeatedly scanning the full panel.
    signal_frame = pl.DataFrame({"signal_date": signal_dates})
    symbols = clean.select("symbol").unique()
    grid = (
        signal_frame.join(symbols, how="cross")
        .with_columns((pl.col("signal_date") - timedelta(days=1)).alias("asof_date"))
        .sort(["symbol", "asof_date"])
    )
    right = feature_panel.with_columns(pl.col("date").alias("asof_date")).sort(
        ["symbol", "asof_date"]
    )
    asof = grid.join_asof(
        right,
        left_on="asof_date",
        right_on="asof_date",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    eligible = (
        asof.filter(pl.col("date").is_not_null())
        .filter(pl.col("history_before") >= max(min_history_sessions, 252))
        .filter(pl.col("positive_count") >= math.ceil(0.8 * liquidity_lookback))
        .filter((pl.col("adv") > 0) & (pl.col("close") > 0))
        .drop_nulls(["momentum"])
    )
    if eligible.is_empty():
        empty = pl.DataFrame(
            schema={
                "signal_date": pl.Date,
                "observation_date": pl.Date,
                "symbol": pl.String,
                "adv": pl.Float64,
                "momentum": pl.Float64,
                "weight": pl.Float64,
            }
        )
        return {}, market_sessions, empty

    universe = (
        eligible.sort(["signal_date", "adv", "symbol"], descending=[False, True, False])
        .group_by("signal_date", maintain_order=True)
        .head(universe_size)
    )
    selected_rows: list[pl.DataFrame] = []
    targets: dict[date, dict[str, float]] = {}
    for key, frame in universe.group_by("signal_date", maintain_order=True):
        signal_date = key[0] if isinstance(key, tuple) else key
        holdings = max(1, math.ceil(frame.height * selection_pct))
        chosen = frame.sort(["momentum", "symbol"], descending=[True, False]).head(holdings)
        weight = 1.0 / chosen.height
        audit = chosen.select(
            pl.col("signal_date"),
            pl.col("date").alias("observation_date"),
            pl.col("symbol"),
            pl.col("adv"),
            pl.col("momentum"),
            pl.lit(weight).alias("weight"),
        )
        selected_rows.append(audit)
        targets[signal_date] = {symbol: weight for symbol in chosen["symbol"].to_list()}
    audit_frame = pl.concat(selected_rows).sort(["signal_date", "symbol"]) if selected_rows else pl.DataFrame()
    return targets, market_sessions, audit_frame


def _percentiles(values: np.ndarray, *, higher_is_better: bool = True) -> np.ndarray:
    if len(values) == 1:
        return np.ones(1)
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    pct = ranks / (len(values) - 1)
    return pct if higher_is_better else 1.0 - pct


def _cap_weights(raw: dict[str, float], cap: float) -> dict[str, float]:
    if not raw:
        return {}
    weights = {symbol: max(0.0, float(value)) for symbol, value in raw.items()}
    total = sum(weights.values())
    if total <= 0:
        weights = {symbol: 1.0 / len(weights) for symbol in weights}
    else:
        weights = {symbol: value / total for symbol, value in weights.items()}
    if cap * len(weights) < 1.0 - 1e-12:
        raise ValueError("Weight cap is infeasible for the number of holdings")
    free = set(weights)
    fixed: dict[str, float] = {}
    while free:
        remaining = 1.0 - sum(fixed.values())
        free_total = sum(weights[symbol] for symbol in free)
        proposed = {
            symbol: remaining * weights[symbol] / free_total if free_total > 0 else remaining / len(free)
            for symbol in free
        }
        breached = {symbol for symbol, value in proposed.items() if value > cap + 1e-12}
        if not breached:
            fixed.update(proposed)
            break
        for symbol in breached:
            fixed[symbol] = cap
            free.remove(symbol)
    return fixed


def _equal_weights(symbols: list[str]) -> dict[str, float]:
    return {symbol: 1.0 / len(symbols) for symbol in symbols} if symbols else {}


def build_targets(
    features: pl.DataFrame,
    membership: dict[date, set[str]],
    config: ResearchConfig,
) -> dict[str, dict[date, dict[str, float]]]:
    targets: dict[str, dict[date, dict[str, float]]] = {
        "equal_weight_universe": {},
        "momentum_12_1_top20": {},
        "high_52w_top20": {},
        "alpha_low_vol_top30": {},
        "high_52w_alpha_low_vol_top30": {},
    }
    for frame in features.partition_by("date", maintain_order=True):
        signal_date = frame["date"][0]
        eligible = (
            frame.filter(pl.col("symbol").is_in(sorted(membership[signal_date])))
            .drop_nulls(["adv63", "momentum_12_1", "high_52w_score"])
            .filter((pl.col("close") > 0) & (pl.col("adv63") > 0))
            .sort(["adv63", "symbol"], descending=[True, False])
            .head(config.universe_size)
        )
        if eligible.is_empty():
            continue
        universe_symbols = eligible["symbol"].to_list()
        targets["equal_weight_universe"][signal_date] = _equal_weights(universe_symbols)

        momentum = eligible.sort(["momentum_12_1", "symbol"], descending=[True, False]).head(
            config.momentum_holdings
        )
        targets["momentum_12_1_top20"][signal_date] = _equal_weights(momentum["symbol"].to_list())

        high = eligible.sort(["high_52w_score", "symbol"], descending=[True, False]).head(
            config.high_52w_holdings
        )
        targets["high_52w_top20"][signal_date] = _equal_weights(high["symbol"].to_list())

        alpha_universe = (
            eligible.drop_nulls(["jensen_alpha", "volatility"])
            .filter(pl.col("volatility") > 0)
            .head(config.alpha_universe_size)
        )
        symbols = alpha_universe["symbol"].to_list()
        alpha_pct = _percentiles(alpha_universe["jensen_alpha"].to_numpy())
        low_vol_pct = _percentiles(alpha_universe["volatility"].to_numpy(), higher_is_better=False)
        alpha_low_vol = 0.5 * alpha_pct + 0.5 * low_vol_pct
        score_by_symbol = dict(zip(symbols, alpha_low_vol, strict=True))
        selected = sorted(symbols, key=lambda symbol: (-score_by_symbol[symbol], symbol))[: config.alpha_holdings]
        minimum_for_cap = math.ceil(1.0 / config.alpha_weight_cap)
        if signal_date.month in {6, 12} and len(selected) >= minimum_for_cap:
            targets["alpha_low_vol_top30"][signal_date] = _cap_weights(
                {symbol: score_by_symbol[symbol] for symbol in selected}, config.alpha_weight_cap
            )

        combined_universe = eligible.drop_nulls(["jensen_alpha", "volatility"]).filter(
            pl.col("volatility") > 0
        )
        combined_symbols = combined_universe["symbol"].to_list()
        high_pct = _percentiles(combined_universe["high_52w_score"].to_numpy())
        all_alpha_pct = _percentiles(combined_universe["jensen_alpha"].to_numpy())
        all_low_vol_pct = _percentiles(
            combined_universe["volatility"].to_numpy(), higher_is_better=False
        )
        combined = 0.5 * high_pct + 0.5 * (0.5 * all_alpha_pct + 0.5 * all_low_vol_pct)
        combined_by_symbol = dict(zip(combined_symbols, combined, strict=True))
        combined_selected = sorted(
            combined_symbols, key=lambda symbol: (-combined_by_symbol[symbol], symbol)
        )[: config.combined_holdings]
        targets["high_52w_alpha_low_vol_top30"][signal_date] = _equal_weights(combined_selected)
    return targets


def _price_maps(panel: pl.DataFrame) -> tuple[dict[date, dict[str, tuple[float, float]]], dict[str, float]]:
    by_date: dict[date, dict[str, tuple[float, float]]] = defaultdict(dict)
    last: dict[str, float] = {}
    for row in panel.iter_rows(named=True):
        symbol = str(row["symbol"])
        open_price = float(row["open"])
        close_price = float(row["close"])
        by_date[row["date"]][symbol] = (open_price, close_price)
        last[symbol] = close_price
    return by_date, last


def _performance_metrics(name: str, daily: pl.DataFrame, orders: pl.DataFrame, config: ResearchConfig) -> dict[str, Any]:
    values = daily["equity"].to_numpy()
    dates = daily["date"].to_list()
    years = max((dates[-1] - dates[0]).days / 365.2425, 1e-9)
    cagr = (float(values[-1]) / float(values[0])) ** (1.0 / years) - 1.0
    peaks = np.maximum.accumulate(values)
    max_drawdown = abs(float(np.min(values / peaks - 1.0)))
    returns = np.diff(values) / values[:-1]
    sharpe = float(np.mean(returns) / np.std(returns, ddof=1) * math.sqrt(252.0)) if len(returns) > 2 and np.std(returns, ddof=1) > 0 else 0.0
    traded = float(orders["gross_value"].sum()) if orders.height else 0.0
    costs = float(orders["friction_cost"].sum()) if orders.height else 0.0
    return {
        "strategy": name,
        "start": dates[0].isoformat(),
        "end": dates[-1].isoformat(),
        "start_equity": float(values[0]),
        "final_equity": float(values[-1]),
        "total_return_pct": (float(values[-1]) / float(values[0]) - 1.0) * 100.0,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "calmar": cagr / max_drawdown if max_drawdown > 1e-12 else 50.0,
        "sharpe": sharpe,
        "orders": orders.height,
        "annualized_turnover_x": traded / float(np.mean(values)) / years,
        "friction_cost": costs,
    }


def simulate_rebalanced_portfolio(
    name: str,
    targets: dict[date, dict[str, float]],
    panel: pl.DataFrame,
    calendar: list[date],
    config: ResearchConfig,
) -> PortfolioResult:
    prices, _ = _price_maps(panel)
    next_session = {calendar[index]: calendar[index + 1] for index in range(len(calendar) - 1)}
    execution_targets = {
        next_session[signal_date]: (signal_date, weights)
        for signal_date, weights in targets.items()
        if signal_date in next_session and next_session[signal_date] <= config.end
    }
    slip = config.slippage_bps / 10_000.0
    buy_factor = (1.0 + slip) * 1.00118658
    sell_factor = (1.0 - slip) * 0.99896342
    dp_charge = 16.0

    cash = config.initial_capital
    holdings: dict[str, Holding] = {}
    daily_rows: list[dict[str, Any]] = []
    order_rows: list[dict[str, Any]] = []
    for today in [value for value in calendar if config.start <= value <= config.end]:
        today_prices = prices.get(today, {})
        for symbol, holding in holdings.items():
            if symbol in today_prices:
                holding.last_price = today_prices[symbol][1]

        scheduled = execution_targets.get(today)
        if scheduled is not None:
            signal_date, weights = scheduled
            executable = {
                symbol: weight
                for symbol, weight in weights.items()
                if symbol in today_prices and today_prices[symbol][0] > 0
            }
            total_weight = sum(executable.values())
            executable = {symbol: weight / total_weight for symbol, weight in executable.items()} if total_weight else {}
            open_equity = cash + sum(
                holding.shares * (today_prices[symbol][0] if symbol in today_prices else holding.last_price)
                for symbol, holding in holdings.items()
            )
            investable = open_equity / buy_factor
            target_shares = {
                symbol: int((investable * weight) // today_prices[symbol][0])
                for symbol, weight in executable.items()
            }
            for symbol in sorted(set(holdings) | set(target_shares)):
                current = holdings.get(symbol, Holding(0, 0.0)).shares
                target = target_shares.get(symbol, 0)
                if target >= current or symbol not in today_prices:
                    continue
                quantity = current - target
                raw = today_prices[symbol][0]
                gross = quantity * raw
                proceeds = gross * sell_factor - dp_charge
                friction = gross - proceeds
                cash += proceeds
                order_rows.append(
                    {"signal_date": signal_date, "execution_date": today, "strategy": name, "symbol": symbol, "side": "sell", "shares": quantity, "raw_price": raw, "gross_value": gross, "friction_cost": friction}
                )
                if target == 0:
                    del holdings[symbol]
                else:
                    holdings[symbol].shares = target
            for symbol in sorted(target_shares):
                current = holdings.get(symbol, Holding(0, 0.0)).shares
                wanted = target_shares[symbol] - current
                if wanted <= 0:
                    continue
                raw = today_prices[symbol][0]
                per_share = raw * buy_factor
                quantity = min(wanted, int(cash // per_share))
                if quantity <= 0:
                    continue
                gross = quantity * raw
                cost = quantity * per_share
                cash -= cost
                order_rows.append(
                    {"signal_date": signal_date, "execution_date": today, "strategy": name, "symbol": symbol, "side": "buy", "shares": quantity, "raw_price": raw, "gross_value": gross, "friction_cost": cost - gross}
                )
                if symbol in holdings:
                    holdings[symbol].shares += quantity
                    holdings[symbol].last_price = today_prices[symbol][1]
                else:
                    holdings[symbol] = Holding(quantity, today_prices[symbol][1])

        equity = cash + sum(holding.shares * holding.last_price for holding in holdings.values())
        daily_rows.append({"date": today, "equity": equity, "cash": cash, "positions": len(holdings)})
    daily = pl.DataFrame(daily_rows)
    order_schema = {
        "signal_date": pl.Date,
        "execution_date": pl.Date,
        "strategy": pl.String,
        "symbol": pl.String,
        "side": pl.String,
        "shares": pl.Int64,
        "raw_price": pl.Float64,
        "gross_value": pl.Float64,
        "friction_cost": pl.Float64,
    }
    orders = pl.DataFrame(order_rows, schema=order_schema) if order_rows else pl.DataFrame(schema=order_schema)
    return PortfolioResult(name, daily, orders, _performance_metrics(name, daily, orders, config))


def _benchmark_result(path: Path, config: ResearchConfig) -> dict[str, Any]:
    frame = (
        pl.read_csv(path, try_parse_dates=True)
        .filter((pl.col("date") >= config.start) & (pl.col("date") <= config.end))
        .sort("date")
    )
    scaled = frame.with_columns((pl.col("tri") / pl.col("tri").first() * config.initial_capital).alias("equity"))
    return _performance_metrics("nifty_500_tri", scaled.select("date", "equity"), pl.DataFrame(), config)


def _sepa_metrics(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    strategy = json.loads(path.read_text())["strategy"]
    return {
        "strategy": "sepa_canonical",
        "cagr": float(strategy["cagr"]),
        "max_drawdown": abs(float(strategy["max_drawdown_pct"])),
        "calmar": float(strategy["calmar"]),
        "orders": int(strategy["filled_trades"]),
        "final_equity": float(strategy["end_value"]),
    }


def _comparison_curves(results: list[PortfolioResult], config: ResearchConfig) -> dict[str, pl.DataFrame]:
    curves = {result.name: result.daily_equity.select("date", "equity") for result in results}
    nifty = (
        pl.read_csv(DEFAULT_NIFTY500_TRI, try_parse_dates=True)
        .filter((pl.col("date") >= config.start) & (pl.col("date") <= config.end))
        .sort("date")
        .with_columns((pl.col("tri") / pl.col("tri").first() * config.initial_capital).alias("equity"))
        .select("date", "equity")
    )
    curves["nifty_500_tri"] = nifty
    sepa_curve_path = DEFAULT_SEPA_SUMMARY.parent / "strategy_daily_equity.csv"
    if sepa_curve_path.exists():
        curves["sepa_canonical"] = (
            pl.read_csv(sepa_curve_path, try_parse_dates=True)
            .filter((pl.col("date") >= config.start) & (pl.col("date") <= config.end))
            .select("date", "equity")
            .sort("date")
        )
    return curves


def _period_comparison(results: list[PortfolioResult], config: ResearchConfig) -> list[dict[str, Any]]:
    periods = [
        ("2010_2014", config.start, date(2014, 12, 31)),
        ("2015_2019", date(2015, 1, 1), date(2019, 12, 31)),
        ("2020_2026", date(2020, 1, 1), config.end),
    ]
    rows: list[dict[str, Any]] = []
    for name, curve in _comparison_curves(results, config).items():
        for label, start, end in periods:
            sliced = curve.filter((pl.col("date") >= start) & (pl.col("date") <= end)).sort("date")
            if sliced.height < 20:
                continue
            metrics = _performance_metrics(name, sliced, pl.DataFrame(), config)
            rows.append(
                {
                    "strategy": name,
                    "period": label,
                    "start": metrics["start"],
                    "end": metrics["end"],
                    "cagr": metrics["cagr"],
                    "max_drawdown": metrics["max_drawdown"],
                    "calmar": metrics["calmar"],
                    "sharpe": metrics["sharpe"],
                }
            )
    return rows


def _write_report(
    config: ResearchConfig,
    results: list[PortfolioResult],
    benchmark: dict[str, Any],
    sepa: dict[str, Any] | None,
    source_dates: dict[date, date],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [result.metrics for result in results] + [benchmark]
    if sepa:
        rows.append(sepa)
    pl.DataFrame(rows, infer_schema_length=None).write_csv(output_dir / "comparison.csv")
    for result in results:
        result.daily_equity.write_csv(output_dir / f"{result.name}_daily_equity.csv")
        result.orders.write_csv(output_dir / f"{result.name}_orders.csv")
    period_rows = _period_comparison(results, config)
    pl.DataFrame(period_rows).write_csv(output_dir / "period_comparison.csv")
    payload = {
        "config": {key: value.isoformat() if isinstance(value, date) else value for key, value in asdict(config).items()},
        "universe": {
            "method": "latest NSE EQ snapshot on/before signal date, then top trailing-63-session turnover",
            "proxy_warning": "This is point-in-time and survivorship-aware, but is not exact historical Nifty 200 membership because that file is unavailable locally.",
            "snapshot_max_lag_days": max(
                (signal - source).days for signal, source in source_dates.items()
            ),
        },
        "results": rows,
        "period_results": period_rows,
    }
    (output_dir / "summary.json").write_text(json.dumps(payload, indent=2))
    ranked = sorted(rows, key=lambda row: float(row.get("calmar", -math.inf)), reverse=True)
    lines = [
        "# Daily factor comparison",
        "",
        "All portfolio strategies use adjusted daily prices, close-to-next-open signals, 10 bps slippage per side, canonical SEPA NSE charge multipliers, and no cash yield.",
        "",
        "The trading universe is a point-in-time proxy: the latest historical NSE EQ snapshot, ranked monthly by trailing 63-session turnover. It is not exact historical Nifty 200 membership.",
        "",
        "| Strategy | CAGR | Max DD | Calmar | Sharpe |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in ranked:
        lines.append(
            f"| {row['strategy']} | {float(row['cagr'])*100:.2f}% | {float(row['max_drawdown'])*100:.2f}% | {float(row['calmar']):.2f} | "
            + (f"{float(row['sharpe']):.2f}" if row.get("sharpe") is not None else "—")
            + " |"
        )
    period_lookup = {
        (row["strategy"], row["period"]): row for row in period_rows
    }
    lines.extend(
        [
            "",
            "## Calmar by market slice",
            "",
            "| Strategy | 2010–14 | 2015–19 | 2020–26 |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in ranked:
        name = row["strategy"]
        values = [period_lookup.get((name, period)) for period in ("2010_2014", "2015_2019", "2020_2026")]
        if not any(values):
            continue
        cells = [f"{float(value['calmar']):.2f}" if value else "—" for value in values]
        lines.append(f"| {name} | {cells[0]} | {cells[1]} | {cells[2]} |")
    (output_dir / "report.md").write_text("\n".join(lines) + "\n")


def run_research(config: ResearchConfig = ResearchConfig(), output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    features, calendar, source_dates = load_and_build_features(config)
    membership, _ = load_point_in_time_membership(features["date"].unique().sort().to_list())
    targets = build_targets(features, membership, config)
    trade_symbols = sorted({symbol for strategy in targets.values() for weights in strategy.values() for symbol in weights})
    panel = (
        pl.scan_parquet(DEFAULT_OHLCV)
        .select("date", "symbol", "open", "close")
        .filter((pl.col("date") >= config.start) & (pl.col("date") <= config.end))
        .filter(pl.col("symbol").is_in(trade_symbols))
        .collect()
        .sort(["date", "symbol"])
    )
    results = [
        simulate_rebalanced_portfolio(name, strategy_targets, panel, calendar, config)
        for name, strategy_targets in targets.items()
    ]
    benchmark = _benchmark_result(DEFAULT_NIFTY500_TRI, config)
    sepa = _sepa_metrics(DEFAULT_SEPA_SUMMARY)
    _write_report(config, results, benchmark, sepa, source_dates, output_dir)
    return {"results": [result.metrics for result in results], "benchmark": benchmark, "sepa": sepa}
