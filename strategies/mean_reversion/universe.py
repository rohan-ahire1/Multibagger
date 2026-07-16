from __future__ import annotations

from pathlib import Path

import polars as pl

from strategies.mean_reversion.indicators import adv


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
    """Load static Nifty 100 symbols, with local CSV and ADV fallbacks."""
    nifty100_path = reference_dir / "nifty100_constituents.csv"
    if nifty100_path.exists():
        frame = pl.read_csv(nifty100_path)
        col = _symbol_column(frame.columns)
        if col is not None:
            return {str(symbol).strip() for symbol in frame[col].to_list() if str(symbol).strip()}

    nifty500_path = reference_dir / "nifty500_constituents.csv"
    if nifty500_path.exists() and ohlcv_path.exists():
        frame = pl.read_csv(nifty500_path)
        col = _symbol_column(frame.columns)
        if col is not None:
            candidates = {str(symbol).strip() for symbol in frame[col].to_list() if str(symbol).strip()}
            ohlcv = pl.read_parquet(ohlcv_path, columns=["date", "symbol", "close", "volume"]).filter(
                pl.col("symbol").is_in(sorted(candidates))
            )
            if not ohlcv.is_empty():
                rows: list[dict[str, float | str]] = []
                for group in ohlcv.sort(["symbol", "date"]).partition_by("symbol", maintain_order=True):
                    symbol = str(group["symbol"][0])
                    adv_values = adv(group["volume"].to_list(), group["close"].to_list(), 50)
                    latest_adv = next((value for value in reversed(adv_values) if value == value), 0.0)
                    rows.append({"symbol": symbol, "adv50": float(latest_adv)})
                ranked = pl.DataFrame(rows).sort("adv50", descending=True).head(100)
                return set(ranked["symbol"].to_list())

    return set(NIFTY_100_SYMBOLS)
