from __future__ import annotations

from datetime import date

import polars as pl

from sepa_scanner.backtest.models import BacktestTrade


def analyze_post_entry_returns(
    trades: list[BacktestTrade],
    ohlcv_df: pl.DataFrame,
    forward_days: int = 10,
) -> list[dict]:
    filled = [t for t in trades if t.entry_date is not None and t.entry_price is not None]
    if not filled:
        return [
            {"day": d, "avg_return_pct": 0.0, "profitable_count": 0, "total": 0, "profitable_pct": 0.0}
            for d in range(1, forward_days + 1)
        ]

    symbols = list({t.symbol for t in filled})
    prices_by_sym: dict[str, pl.DataFrame] = {}
    for sym in symbols:
        prices_by_sym[sym] = ohlcv_df.filter(pl.col("symbol") == sym).sort("date")

    per_day: list[list[float]] = [[] for _ in range(forward_days)]

    for trade in filled:
        sym_prices = prices_by_sym.get(trade.symbol)
        if sym_prices is None or sym_prices.is_empty():
            continue

        entry_price = trade.entry_price
        assert entry_price is not None
        assert trade.entry_date is not None

        future = sym_prices.filter(pl.col("date") > pl.lit(trade.entry_date)).head(forward_days)
        closes = future["close"].to_list()

        for i, c in enumerate(closes):
            ret_pct = (float(c) - entry_price) / entry_price * 100.0
            per_day[i].append(ret_pct)

    result = []
    for day_idx in range(forward_days):
        rets = per_day[day_idx]
        if not rets:
            result.append({
                "day": day_idx + 1,
                "avg_return_pct": 0.0,
                "profitable_count": 0,
                "total": 0,
                "profitable_pct": 0.0,
            })
        else:
            profitable = sum(1 for r in rets if r > 0)
            result.append({
                "day": day_idx + 1,
                "avg_return_pct": sum(rets) / len(rets),
                "profitable_count": profitable,
                "total": len(rets),
                "profitable_pct": profitable / len(rets) * 100.0,
            })
    return result


def analyze_signal_quality(
    signals_df: pl.DataFrame,
    ohlcv_df: pl.DataFrame,
    pending_days: int = 5,
) -> dict:
    breakout = signals_df.filter(pl.col("signal_type") == "BREAKOUT")
    if breakout.is_empty():
        return _empty_result(pending_days)

    symbols = breakout["symbol"].unique().to_list()
    ohlcv = ohlcv_df.filter(pl.col("symbol").is_in(symbols)).sort(["symbol", "date"])
    prices_by_sym: dict[str, pl.DataFrame] = {}
    for sym in symbols:
        prices_by_sym[sym] = ohlcv.filter(pl.col("symbol") == sym)

    results: list[dict] = []

    for row in breakout.iter_rows(named=True):
        sym = str(row["symbol"])
        sig_date = row["signal_date"]
        entry_ref = float(row["entry_ref"])

        sym_prices = prices_by_sym.get(sym)
        if sym_prices is None or sym_prices.is_empty():
            continue

        future = sym_prices.filter(pl.col("date") > pl.lit(sig_date)).head(pending_days)
        if future.is_empty():
            continue

        sig_day = sym_prices.filter(pl.col("date") == pl.lit(sig_date))
        sig_close = (
            float(sig_day["close"][0])
            if not sig_day.is_empty()
            else entry_ref
        )

        day_highs = future["high"].to_list()
        day_closes = future["close"].to_list()
        max_high = max(float(h) for h in day_highs)

        reached = max_high >= entry_ref
        day_reached: int | None = None
        for i, h in enumerate(day_highs):
            if float(h) >= entry_ref:
                day_reached = i + 1
                break

        max_gain_pct = (max_high - sig_close) / sig_close * 100.0
        min_low = min(float(x) for x in future["low"].to_list())
        max_loss_pct = (min_low - sig_close) / sig_close * 100.0

        last_close = float(day_closes[-1]) if day_closes else sig_close
        last_day_return_pct = (last_close - sig_close) / sig_close * 100.0
        profitable_last = last_close > sig_close

        fwd_returns = [
            (float(c) - sig_close) / sig_close * 100.0
            for c in day_closes
        ]

        next_open: float | None = None
        next_row = future.head(1)
        if not next_row.is_empty():
            next_open = float(next_row["open"][0])

        results.append({
            "symbol": sym,
            "signal_date": sig_date,
            "entry_ref": entry_ref,
            "sig_close": sig_close,
            "reached": reached,
            "day_reached": day_reached,
            "max_gain_pct": max_gain_pct,
            "max_loss_pct": max_loss_pct,
            "last_day_return_pct": last_day_return_pct,
            "profitable_last": profitable_last,
            "fwd_returns": fwd_returns,
            "next_open": next_open,
        })

    if not results:
        return _empty_result(pending_days)

    total = len(results)
    pivot_reached = sum(1 for r in results if r["reached"])
    pivot_not_reached = total - pivot_reached
    pivot_reached_pct = (pivot_reached / total * 100.0) if total > 0 else 0.0

    day_reached_counts: dict[int, int] = {}
    for r in results:
        if r["day_reached"] is not None:
            d = r["day_reached"]
            day_reached_counts[d] = day_reached_counts.get(d, 0) + 1

    day_reached_breakdown = [
        {
            "day": d,
            "count": day_reached_counts.get(d, 0),
            "pct_of_reached": (
                day_reached_counts.get(d, 0) / pivot_reached * 100.0
                if pivot_reached > 0
                else 0.0
            ),
        }
        for d in range(1, pending_days + 1)
    ]

    profitable_at_last_day = sum(1 for r in results if r["profitable_last"])
    profitable_at_last_day_pct = (profitable_at_last_day / total * 100.0) if total > 0 else 0.0

    last_returns = [r["last_day_return_pct"] for r in results]
    avg_last_day_return_pct = sum(last_returns) / len(last_returns) if last_returns else 0.0
    sorted_last = sorted(last_returns)
    median_last_day_return_pct = (
        sorted_last[len(sorted_last) // 2]
        if sorted_last
        else 0.0
    )

    max_gains = [r["max_gain_pct"] for r in results]
    max_losses = [r["max_loss_pct"] for r in results]
    avg_max_gain_pct = sum(max_gains) / len(max_gains) if max_gains else 0.0
    avg_max_loss_pct = sum(max_losses) / len(max_losses) if max_losses else 0.0

    per_day_returns = []
    for day_idx in range(1, pending_days + 1):
        day_rets = [
            r["fwd_returns"][day_idx - 1]
            for r in results
            if len(r["fwd_returns"]) >= day_idx
        ]
        if not day_rets:
            per_day_returns.append({
                "day": day_idx,
                "avg_return_pct": 0.0,
                "positive_count": 0,
                "total": 0,
                "positive_pct": 0.0,
            })
        else:
            pos = sum(1 for x in day_rets if x > 0)
            per_day_returns.append({
                "day": day_idx,
                "avg_return_pct": sum(day_rets) / len(day_rets),
                "positive_count": pos,
                "total": len(day_rets),
                "positive_pct": (pos / len(day_rets) * 100.0),
            })

    gap_pcts = [
        (r["next_open"] - r["sig_close"]) / r["sig_close"] * 100.0
        for r in results
        if r["next_open"] is not None
    ]
    gap_total = len(gap_pcts)
    gap_up_count = sum(1 for g in gap_pcts if g > 0)
    gap_up_pct = (gap_up_count / gap_total * 100.0) if gap_total > 0 else 0.0
    avg_gap_pct = sum(gap_pcts) / len(gap_pcts) if gap_pcts else 0.0
    big_gap_up_count = sum(1 for g in gap_pcts if g >= 2.0)
    big_gap_down_count = sum(1 for g in gap_pcts if g <= -2.0)

    unfilled = [r for r in results if not r["reached"]]
    if unfilled:
        unfilled_max_gains = [r["max_gain_pct"] for r in unfilled]
        unfilled_last_returns = [r["last_day_return_pct"] for r in unfilled]
        unfilled_profitable = sum(1 for r in unfilled if r["profitable_last"])
        unfilled_count = len(unfilled)
        unfilled_analysis = {
            "count": unfilled_count,
            "avg_max_gain_pct": sum(unfilled_max_gains) / len(unfilled_max_gains),
            "avg_last_day_return_pct": sum(unfilled_last_returns) / len(unfilled_last_returns),
            "profitable_at_last_day": unfilled_profitable,
            "profitable_at_last_day_pct": (
                unfilled_profitable / unfilled_count * 100.0
            ),
        }
    else:
        unfilled_analysis = {
            "count": 0,
            "avg_max_gain_pct": 0.0,
            "avg_last_day_return_pct": 0.0,
            "profitable_at_last_day": 0,
            "profitable_at_last_day_pct": 0.0,
        }

    return {
        "total_with_data": total,
        "pivot_reached": pivot_reached,
        "pivot_not_reached": pivot_not_reached,
        "pivot_reached_pct": pivot_reached_pct,
        "day_reached_breakdown": day_reached_breakdown,
        "profitable_at_last_day": profitable_at_last_day,
        "profitable_at_last_day_pct": profitable_at_last_day_pct,
        "avg_last_day_return_pct": avg_last_day_return_pct,
        "median_last_day_return_pct": median_last_day_return_pct,
        "avg_max_gain_pct": avg_max_gain_pct,
        "avg_max_loss_pct": avg_max_loss_pct,
        "per_day_returns": per_day_returns,
        "gap_analysis": {
            "gap_up_count": gap_up_count,
            "total": gap_total,
            "gap_up_pct": gap_up_pct,
            "avg_gap_pct": avg_gap_pct,
            "big_gap_up_count": big_gap_up_count,
            "big_gap_down_count": big_gap_down_count,
        },
        "unfilled_analysis": unfilled_analysis,
    }


def _empty_result(pending_days: int) -> dict:
    return {
        "total_with_data": 0,
        "pivot_reached": 0,
        "pivot_not_reached": 0,
        "pivot_reached_pct": 0.0,
        "day_reached_breakdown": [
            {"day": d, "count": 0, "pct_of_reached": 0.0}
            for d in range(1, pending_days + 1)
        ],
        "profitable_at_last_day": 0,
        "profitable_at_last_day_pct": 0.0,
        "avg_last_day_return_pct": 0.0,
        "median_last_day_return_pct": 0.0,
        "avg_max_gain_pct": 0.0,
        "avg_max_loss_pct": 0.0,
        "per_day_returns": [
            {
                "day": d,
                "avg_return_pct": 0.0,
                "positive_count": 0,
                "total": 0,
                "positive_pct": 0.0,
            }
            for d in range(1, pending_days + 1)
        ],
        "gap_analysis": {
            "gap_up_count": 0,
            "total": 0,
            "gap_up_pct": 0.0,
            "avg_gap_pct": 0.0,
            "big_gap_up_count": 0,
            "big_gap_down_count": 0,
        },
        "unfilled_analysis": {
            "count": 0,
            "avg_max_gain_pct": 0.0,
            "avg_last_day_return_pct": 0.0,
            "profitable_at_last_day": 0,
            "profitable_at_last_day_pct": 0.0,
        },
    }
