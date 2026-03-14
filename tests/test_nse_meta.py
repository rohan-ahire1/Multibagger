from __future__ import annotations

import polars as pl

from sepa_scanner.data_sources.nse_meta import apply_india_filters


def test_excludes_asm_gsm_symbols() -> None:
    df = pl.DataFrame({
        "symbol": ["GOOD", "BAD", "OK"],
        "adv50": [100_000_000.0, 200_000_000.0, 80_000_000.0],
    })
    result = apply_india_filters(
        df, asm_gsm_symbols={"BAD"}, min_adtv_cr=5.0,
    )
    assert "BAD" not in result["symbol"].to_list()
    assert "GOOD" in result["symbol"].to_list()


def test_filters_by_adtv_threshold() -> None:
    df = pl.DataFrame({
        "symbol": ["HIGH", "LOW"],
        "adv50": [100_000_000.0, 10_000_000.0],  # 10Cr vs 1Cr
    })
    result = apply_india_filters(df, min_adtv_cr=5.0)
    assert result["symbol"].to_list() == ["HIGH"]


def test_fno_stocks_sorted_first() -> None:
    df = pl.DataFrame({
        "symbol": ["NONFNO", "FNO1", "FNO2"],
        "adv50": [100_000_000.0, 100_000_000.0, 100_000_000.0],
    })
    result = apply_india_filters(df, fno_symbols={"FNO1", "FNO2"}, min_adtv_cr=1.0)
    assert result["symbol"].to_list()[:2] == ["FNO1", "FNO2"] or set(result["symbol"].to_list()[:2]) == {"FNO1", "FNO2"}
