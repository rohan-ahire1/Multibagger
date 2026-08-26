from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

NIFTY_100_SYMBOLS = {
    "ABB",
    "ACC",
    "ADANIENT",
    "ADANIPORTS",
    "AMBUJACEM",
    "APOLLOHOSP",
    "ASIANPAINT",
    "ATGL",
    "AXISBANK",
    "BAJAJ-AUTO",
    "BAJAJFINSV",
    "BAJFINANCE",
    "BANKBARODA",
    "BEL",
    "BHEL",
    "BHARTIARTL",
    "BPCL",
    "BRITANNIA",
    "CANBK",
    "CGPOWER",
    "CIPLA",
    "COALINDIA",
    "COLPAL",
    "CUMMINSIND",
    "DABUR",
    "DIVISLAB",
    "DLF",
    "DMART",
    "DRREDDY",
    "EICHERMOT",
    "GAIL",
    "GODREJCP",
    "GRASIM",
    "HAL",
    "HAVELLS",
    "HCLTECH",
    "HDFCBANK",
    "HDFCLIFE",
    "HEROMOTOCO",
    "HINDALCO",
    "HINDUNILVR",
    "ICICIBANK",
    "IDFCFIRSTB",
    "INDIGO",
    "INDUSINDBK",
    "INFY",
    "IOC",
    "IRCTC",
    "ITC",
    "JINDALSTEL",
    "JIOFIN",
    "JSWSTEEL",
    "JUBLFOOD",
    "KOTAKBANK",
    "LICI",
    "LODHA",
    "LT",
    "LTIM",
    "LUPIN",
    "M&M",
    "MANKIND",
    "MARICO",
    "MARUTI",
    "MAXHEALTH",
    "MCDOWELL-N",
    "MOTHERSON",
    "MPHASIS",
    "NAUKRI",
    "NESTLEIND",
    "NTPC",
    "ONGC",
    "PAGEIND",
    "PAYTM",
    "PEL",
    "PERSISTENT",
    "PIDILITIND",
    "PIIND",
    "PNB",
    "POLYCAB",
    "POWERGRID",
    "RECLTD",
    "RELIANCE",
    "SAIL",
    "SBILIFE",
    "SBIN",
    "SHREECEM",
    "SIEMENS",
    "SRF",
    "SUNPHARMA",
    "TATACONSUM",
    "TATAMOTORS",
    "TATASTEEL",
    "TCS",
    "TECHM",
    "TITAN",
    "TORNTPHARM",
    "TRENT",
    "TVSMOTOR",
    "ULTRACEMCO",
    "VEDL",
    "VOLTAS",
    "WIPRO",
    "ZOMATO",
}


def _symbol_column(columns: list[str]) -> str | None:
    for candidate in ("Symbol", "symbol", "SYMBOL"):
        if candidate in columns:
            return candidate
    return None


def load_nifty100_universe(
    *,
    reference_dir: Path = Path("data/reference"),
    ohlcv_path: Path = Path("data/yf_adjusted/ohlcv_all.parquet"),
) -> set[str]:
    """Load a present-day reference list for live-only convenience.

    This function must not be used to select a historical universe.  Static
    constituent files have no effective dates, and the previous ADV fallback
    ranked on the last available observations in the complete sample.
    """
    del ohlcv_path
    nifty100_path = reference_dir / "nifty100_constituents.csv"
    if nifty100_path.exists():
        frame = pl.read_csv(nifty100_path)
        col = _symbol_column(frame.columns)
        if col is not None:
            return {str(symbol).strip() for symbol in frame[col].to_list() if str(symbol).strip()}

    return set(NIFTY_100_SYMBOLS)


def historical_liquidity_universe(
    panel: pl.DataFrame,
    *,
    as_of: date,
    size: int = 100,
    lookback_sessions: int = 50,
    min_history_sessions: int = 200,
    min_price: float = 0.0,
    min_adv_inr: float = 0.0,
) -> set[str]:
    """Rank securities using only observations available strictly before ``as_of``.

    A symbol's later disappearance, later listing, future prices, and future
    volume cannot affect this result.  The rank is deliberately a liquidity
    proxy rather than reconstructed historical Nifty membership, because no
    dated constituent file is available in this repository.
    """
    if size <= 0 or lookback_sessions <= 0 or min_history_sessions <= 0:
        raise ValueError("universe size and lookback/history windows must be positive")
    required = {"date", "symbol", "close", "volume"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"panel missing universe columns: {sorted(missing)}")

    frame = panel.select(
        "date", "symbol", "close", "volume",
        *( ["turnover"] if "turnover" in panel.columns else [] ),
    ).filter(pl.col("date") < pl.lit(as_of))
    if frame.is_empty():
        return set()
    if "turnover" not in frame.columns:
        frame = frame.with_columns((pl.col("close") * pl.col("volume")).alias("turnover"))

    ranked: list[tuple[str, float]] = []
    for symbol, group in frame.sort(["symbol", "date"]).group_by("symbol", maintain_order=True):
        history = group.sort("date")
        if history.height < min_history_sessions:
            continue
        trailing = history.tail(lookback_sessions)
        if trailing.height < lookback_sessions:
            continue
        if trailing.filter((pl.col("turnover") > 0) & (pl.col("volume") > 0)).height < 0.8 * lookback_sessions:
            continue
        trailing_adv = float(trailing["turnover"].median())
        latest_close = float(history["close"][-1])
        if latest_close < min_price or trailing_adv < min_adv_inr:
            continue
        ranked.append((str(symbol[0]), trailing_adv))
    return {symbol for symbol, _ in sorted(ranked, key=lambda item: (-item[1], item[0]))[:size]}


def historical_liquidity_membership(
    panel: pl.DataFrame,
    *,
    size: int = 100,
    lookback_sessions: int = 50,
    min_history_sessions: int = 200,
    reconstitute: str = "semiannual",
) -> pl.DataFrame:
    """Return dated membership snapshots; each snapshot uses prior sessions only."""
    from sepa_scanner.lab.universe import liquidity_universe

    frame = panel
    if "turnover" not in frame.columns:
        frame = frame.with_columns((pl.col("close") * pl.col("volume")).alias("turnover"))
    return liquidity_universe(
        frame,
        size=size,
        lookback_sessions=lookback_sessions,
        min_history_sessions=min_history_sessions,
        reconstitute=reconstitute,  # type: ignore[arg-type]
    )


def filter_signals_to_historical_membership(
    signals: pl.DataFrame,
    membership: pl.DataFrame,
) -> pl.DataFrame:
    """Keep signals whose latest membership snapshot was known on signal date."""
    if signals.is_empty() or membership.is_empty():
        return signals.clear()
    required = {"signal_date", "symbol"}
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"signals missing membership columns: {sorted(missing)}")
    eligible = signals.sort(["symbol", "signal_date"]).join_asof(
        membership.sort(["symbol", "effective_date"]),
        left_on="signal_date",
        right_on="effective_date",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    return eligible.filter(pl.col("effective_date").is_not_null()).drop("effective_date", "rank_by_turnover").sort(
        ["signal_date", "symbol"]
    )
