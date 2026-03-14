from __future__ import annotations

from datetime import date

import polars as pl


def compute_sector_rs(
    frame: pl.DataFrame,
    sector_map: dict[str, str],
    benchmark_symbol: str,
    scan_date: date,
    lookbacks: list[int],
    weights: list[float],
) -> dict[str, float]:
    """Compute sector-level RS scores.

    For each sector, compute the median RS score of its constituent stocks.
    Returns {sector_name: rs_score}.
    """
    sorted_frame = frame.sort(["symbol", "date"])

    # Compute per-stock returns
    return_columns = []
    for lookback in lookbacks:
        ret_col = (
            (pl.col("close") / pl.col("close").shift(lookback)) - 1.0
        ).over("symbol").alias(f"ret_{lookback}")
        return_columns.append(ret_col)

    with_returns = sorted_frame.with_columns(return_columns)

    # Get benchmark returns
    bm_returns = {}
    bm_row = with_returns.filter(
        (pl.col("symbol") == benchmark_symbol) & (pl.col("date") == pl.lit(scan_date))
    )
    if bm_row.is_empty():
        return {}
    for lookback in lookbacks:
        bm_returns[lookback] = bm_row[f"ret_{lookback}"][0]

    # Compute per-stock RS scores
    scan_data = with_returns.filter(
        (pl.col("date") == pl.lit(scan_date)) & (pl.col("symbol") != benchmark_symbol)
    )

    stock_scores: dict[str, float] = {}
    for row in scan_data.to_dicts():
        symbol = row["symbol"]
        if symbol not in sector_map:
            continue
        score = 0.0
        for lookback, weight in zip(lookbacks, weights):
            stock_ret = row.get(f"ret_{lookback}", 0.0) or 0.0
            bm_ret = bm_returns.get(lookback, 0.0) or 0.0
            if (1.0 + bm_ret) != 0:
                score += weight * ((1.0 + stock_ret) / (1.0 + bm_ret) - 1.0)
        stock_scores[symbol] = score

    # Aggregate by sector (median score)
    sector_scores: dict[str, list[float]] = {}
    for symbol, score in stock_scores.items():
        sector = sector_map.get(symbol, "UNKNOWN")
        sector_scores.setdefault(sector, []).append(score)

    from statistics import median as _median

    return {
        sector: _median(scores)
        for sector, scores in sector_scores.items()
        if scores
    }
