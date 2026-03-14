from __future__ import annotations

from pathlib import Path

import polars as pl


def load_fno_list(path: Path | None = None) -> set[str]:
    """Load F&O stock list. Returns set of symbols."""
    if path is None or not Path(path).exists():
        return set()
    df = pl.read_csv(path)
    return set(df["symbol"].to_list())


def load_asm_gsm_list(path: Path | None = None) -> set[str]:
    """Load ASM/GSM surveillance list. Returns set of symbols to exclude."""
    if path is None or not Path(path).exists():
        return set()
    df = pl.read_csv(path)
    return set(df["symbol"].to_list())


def load_sector_map(path: Path | None = None) -> dict[str, str]:
    """Load sector classification. Returns {symbol: sector_name}."""
    if path is None or not Path(path).exists():
        return {}
    df = pl.read_csv(path)
    return dict(zip(df["symbol"].to_list(), df["sector"].to_list()))


def apply_india_filters(
    eligible: pl.DataFrame,
    *,
    fno_symbols: set[str] | None = None,
    asm_gsm_symbols: set[str] | None = None,
    min_market_cap_cr: float = 500.0,
    min_adtv_cr: float = 5.0,
) -> pl.DataFrame:
    """Apply India-specific universe filters to eligible stocks."""
    result = eligible

    # Exclude ASM/GSM stocks
    if asm_gsm_symbols:
        result = result.filter(~pl.col("symbol").is_in(list(asm_gsm_symbols)))

    # Market cap filter (if market_cap column available)
    if "market_cap" in result.columns:
        result = result.filter(pl.col("market_cap") >= min_market_cap_cr * 1e7)

    # ADTV filter: adv50 is in INR, convert threshold from Cr
    # adv50 is always present in eligible universe (computed in _eligible_universe)
    if "adv50" in result.columns:
        result = result.filter(pl.col("adv50") >= min_adtv_cr * 1e7)

    # F&O preference: sort F&O stocks first (don't exclude non-F&O)
    if fno_symbols:
        result = result.with_columns(
            pl.col("symbol").is_in(list(fno_symbols)).alias("is_fno")
        ).sort("is_fno", descending=True)

    return result
