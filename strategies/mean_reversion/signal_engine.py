from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from sepa_scanner.data_sources.pit import assert_pit_signal_inputs
from strategies.mean_reversion.indicators import (
    adx,
    adv,
    atr,
    connors_rsi,
    cumulative_rsi,
    days_down,
    ibs,
    rsi,
    sma,
    volume_percent_rank,
)


@dataclass(slots=True)
class MRSignal:
    signal_date: date
    symbol: str
    close: float
    rsi2: float
    sma200: float
    sma5: float
    entry_ref: float
    stop: float
    atr14: float
    adv50: float


MR_SIGNAL_SCHEMA: dict[str, Any] = {
    "signal_date": pl.Date,
    "symbol": pl.String,
    "close": pl.Float64,
    "rsi2": pl.Float64,
    "sma200": pl.Float64,
    "sma5": pl.Float64,
    "entry_ref": pl.Float64,
    "stop": pl.Float64,
    "atr14": pl.Float64,
    "adv50": pl.Float64,
}


MR_V2_SIGNAL_SCHEMA: dict[str, Any] = {
    "signal_date": pl.Date,
    "symbol": pl.String,
    "close": pl.Float64,
    "rsi2": pl.Float64,
    "crsi": pl.Float64,
    "cum_rsi": pl.Float64,
    "ibs": pl.Float64,
    "adx14": pl.Float64,
    "vol_rank20": pl.Float64,
    "days_down_5": pl.Float64,
    "sma200": pl.Float64,
    "sma5": pl.Float64,
    "sma3": pl.Float64,
    "entry_ref": pl.Float64,
    "stop": pl.Float64,
    "atr14": pl.Float64,
    "adv50": pl.Float64,
}


def scan_mean_reversion(
    ohlcv: pl.DataFrame,
    *,
    rsi_period: int = 2,
    rsi_threshold: float = 10.0,
    sma_trend_period: int = 200,
    min_adv_inr: float = 1e8,
    min_price: float = 50.0,
    stop_atr_mult: float = 2.0,
    universe_symbols: set[str] | None = None,
) -> pl.DataFrame:
    """Scan all symbols on all dates and return Connors RSI2 MR signals."""
    assert_pit_signal_inputs(ohlcv)
    if ohlcv.is_empty():
        return pl.DataFrame(schema=MR_SIGNAL_SCHEMA)

    required = {"date", "symbol", "open", "high", "low", "close", "volume"}
    missing = required - set(ohlcv.columns)
    if missing:
        raise ValueError(f"OHLCV frame missing required columns: {sorted(missing)}")

    rows: list[dict[str, Any]] = []
    frame = ohlcv.sort(["symbol", "date"])
    if universe_symbols is not None:
        frame = frame.filter(pl.col("symbol").is_in(sorted(universe_symbols)))

    for group in frame.partition_by("symbol", maintain_order=True):
        symbol = str(group["symbol"][0])
        dates = group["date"].to_list()
        highs = [float(value) for value in group["high"].to_list()]
        lows = [float(value) for value in group["low"].to_list()]
        closes = [float(value) for value in group["close"].to_list()]
        volumes = [float(value) for value in group["volume"].to_list()]

        rsi_values = rsi(closes, rsi_period)
        sma_trend = sma(closes, sma_trend_period)
        sma5_values = sma(closes, 5)
        atr_values = atr(highs, lows, closes, 14)
        adv_values = adv(volumes, closes, 50)

        for idx, signal_date in enumerate(dates):
            close = closes[idx]
            rsi2 = rsi_values[idx]
            sma200 = sma_trend[idx]
            sma5 = sma5_values[idx]
            atr14 = atr_values[idx]
            adv50 = adv_values[idx]
            if any(math.isnan(value) for value in (rsi2, sma200, sma5, atr14, adv50)):
                continue
            if close <= sma200:
                continue
            if rsi2 >= rsi_threshold:
                continue
            if close <= min_price:
                continue
            if adv50 <= min_adv_inr:
                continue

            rows.append(
                {
                    "signal_date": signal_date,
                    "symbol": symbol,
                    "close": close,
                    "rsi2": rsi2,
                    "sma200": sma200,
                    "sma5": sma5,
                    # Next-session execution belongs to the simulator.  A
                    # signal row cannot embed tomorrow's open without leakage.
                    "entry_ref": close,
                    "stop": close - (atr14 * stop_atr_mult),
                    "atr14": atr14,
                    "adv50": adv50,
                }
            )

    if not rows:
        return pl.DataFrame(schema=MR_SIGNAL_SCHEMA)
    return pl.DataFrame(rows, schema=MR_SIGNAL_SCHEMA).sort(
        by=["signal_date", "rsi2", "symbol"],
        descending=[False, False, False],
    )


def scan_short_mean_reversion(
    ohlcv: pl.DataFrame,
    *,
    rsi_period: int = 2,
    rsi_threshold: float = 65.0,
    sma_trend_period: int = 200,
    min_adv_inr: float = 1e8,
    min_price: float = 50.0,
    stop_atr_mult: float = 3.5,
    universe_symbols: set[str] | None = None,
) -> pl.DataFrame:
    """Scan all symbols for short-side mean reversion signals.

    Mirror of the long setup:
    - RSI(2) > threshold
    - close < SMA(200)
    - stop is ATR above close
    """
    assert_pit_signal_inputs(ohlcv)
    if ohlcv.is_empty():
        return pl.DataFrame(schema=MR_SIGNAL_SCHEMA)

    required = {"date", "symbol", "open", "high", "low", "close", "volume"}
    missing = required - set(ohlcv.columns)
    if missing:
        raise ValueError(f"OHLCV frame missing required columns: {sorted(missing)}")

    rows: list[dict[str, Any]] = []
    frame = ohlcv.sort(["symbol", "date"])
    if universe_symbols is not None:
        frame = frame.filter(pl.col("symbol").is_in(sorted(universe_symbols)))

    for group in frame.partition_by("symbol", maintain_order=True):
        symbol = str(group["symbol"][0])
        dates = group["date"].to_list()
        highs = [float(value) for value in group["high"].to_list()]
        lows = [float(value) for value in group["low"].to_list()]
        closes = [float(value) for value in group["close"].to_list()]
        volumes = [float(value) for value in group["volume"].to_list()]

        rsi_values = rsi(closes, rsi_period)
        sma_trend = sma(closes, sma_trend_period)
        sma5_values = sma(closes, 5)
        atr_values = atr(highs, lows, closes, 14)
        adv_values = adv(volumes, closes, 50)

        for idx, signal_date in enumerate(dates):
            close = closes[idx]
            rsi2 = rsi_values[idx]
            sma200 = sma_trend[idx]
            sma5 = sma5_values[idx]
            atr14 = atr_values[idx]
            adv50 = adv_values[idx]
            if any(math.isnan(value) for value in (rsi2, sma200, sma5, atr14, adv50)):
                continue
            if close >= sma200:
                continue
            if rsi2 <= rsi_threshold:
                continue
            if close <= min_price:
                continue
            if adv50 <= min_adv_inr:
                continue

            rows.append(
                {
                    "signal_date": signal_date,
                    "symbol": symbol,
                    "close": close,
                    "rsi2": rsi2,
                    "sma200": sma200,
                    "sma5": sma5,
                    "entry_ref": close,
                    "stop": close + (atr14 * stop_atr_mult),
                    "atr14": atr14,
                    "adv50": adv50,
                }
            )

    if not rows:
        return pl.DataFrame(schema=MR_SIGNAL_SCHEMA)
    return pl.DataFrame(rows, schema=MR_SIGNAL_SCHEMA).sort(
        by=["signal_date", "rsi2", "symbol"],
        descending=[False, True, False],
    )


def scan_mr_v2(
    ohlcv: pl.DataFrame,
    *,
    entry_indicator: str = "crsi",
    entry_threshold: float = 10.0,
    sma_trend_period: int = 200,
    min_days_down: int | None = None,
    days_down_lookback: int = 5,
    max_ibs: float | None = None,
    max_adx: float | None = None,
    max_vol_rank: float | None = None,
    vol_rank_period: int = 20,
    min_adv_inr: float = 1e8,
    min_price: float = 50.0,
    stop_atr_mult: float = 2.0,
    max_signals_per_day: int | None = None,
    cooldown_days: int = 0,
    universe_symbols: set[str] | None = None,
) -> pl.DataFrame:
    """Scan all symbols with research-driven MR v2 indicator/filter combinations."""
    assert_pit_signal_inputs(ohlcv)
    if entry_indicator not in {"rsi2", "crsi", "cum_rsi"}:
        raise ValueError("entry_indicator must be one of: rsi2, crsi, cum_rsi")
    if cooldown_days < 0:
        raise ValueError("cooldown_days must be >= 0")
    if max_signals_per_day is not None and max_signals_per_day <= 0:
        raise ValueError("max_signals_per_day must be positive when provided")
    if ohlcv.is_empty():
        return pl.DataFrame(schema=MR_V2_SIGNAL_SCHEMA)

    required = {"date", "symbol", "open", "high", "low", "close", "volume"}
    missing = required - set(ohlcv.columns)
    if missing:
        raise ValueError(f"OHLCV frame missing required columns: {sorted(missing)}")

    rows: list[dict[str, Any]] = []
    frame = ohlcv.sort(["symbol", "date"])
    if universe_symbols is not None:
        frame = frame.filter(pl.col("symbol").is_in(sorted(universe_symbols)))

    for group in frame.partition_by("symbol", maintain_order=True):
        symbol = str(group["symbol"][0])
        dates = group["date"].to_list()
        highs = [float(value) for value in group["high"].to_list()]
        lows = [float(value) for value in group["low"].to_list()]
        closes = [float(value) for value in group["close"].to_list()]
        volumes = [float(value) for value in group["volume"].to_list()]

        rsi2_values = rsi(closes, 2)
        crsi_values = connors_rsi(closes)
        cum_rsi_values = cumulative_rsi(closes, 2, 2)
        ibs_values = ibs(highs, lows, closes)
        adx_values = adx(highs, lows, closes, 14)
        vol_rank_values = volume_percent_rank(volumes, vol_rank_period)
        days_down_values = days_down(closes, days_down_lookback)
        sma_trend = sma(closes, sma_trend_period)
        sma5_values = sma(closes, 5)
        sma3_values = sma(closes, 3)
        atr_values = atr(highs, lows, closes, 14)
        adv_values = adv(volumes, closes, 50)

        indicator_values = {
            "rsi2": rsi2_values,
            "crsi": crsi_values,
            "cum_rsi": cum_rsi_values,
        }[entry_indicator]
        last_signal_date: date | None = None

        for idx, signal_date in enumerate(dates):
            close = closes[idx]
            rsi2 = rsi2_values[idx]
            crsi = crsi_values[idx]
            cum_rsi = cum_rsi_values[idx]
            ibs_value = ibs_values[idx]
            adx14 = adx_values[idx]
            vol_rank20 = vol_rank_values[idx]
            days_down_5 = days_down_values[idx]
            sma200 = sma_trend[idx]
            sma5 = sma5_values[idx]
            sma3 = sma3_values[idx]
            atr14 = atr_values[idx]
            adv50 = adv_values[idx]
            entry_value = indicator_values[idx]

            required_values = (entry_value, rsi2, crsi, cum_rsi, ibs_value, sma200, sma5, sma3, atr14, adv50)
            if any(math.isnan(value) for value in required_values):
                continue
            if close <= sma200:
                continue
            if entry_value >= entry_threshold:
                continue
            if close <= min_price:
                continue
            if adv50 <= min_adv_inr:
                continue
            if min_days_down is not None and (math.isnan(days_down_5) or days_down_5 < min_days_down):
                continue
            if max_ibs is not None and ibs_value >= max_ibs:
                continue
            if max_adx is not None and (math.isnan(adx14) or adx14 >= max_adx):
                continue
            if max_vol_rank is not None and (math.isnan(vol_rank20) or vol_rank20 >= max_vol_rank):
                continue
            if last_signal_date is not None and (signal_date - last_signal_date).days < cooldown_days:
                continue

            rows.append(
                {
                    "signal_date": signal_date,
                    "symbol": symbol,
                    "close": close,
                    "rsi2": rsi2,
                    "crsi": crsi,
                    "cum_rsi": cum_rsi,
                    "ibs": ibs_value,
                    "adx14": adx14,
                    "vol_rank20": vol_rank20,
                    "days_down_5": days_down_5,
                    "sma200": sma200,
                    "sma5": sma5,
                    "sma3": sma3,
                    "entry_ref": close,
                    "stop": close - (atr14 * stop_atr_mult),
                    "atr14": atr14,
                    "adv50": adv50,
                }
            )
            last_signal_date = signal_date

    if not rows:
        return pl.DataFrame(schema=MR_V2_SIGNAL_SCHEMA)

    rows.sort(key=lambda row: (row["signal_date"], float(row[entry_indicator]), row["symbol"]))
    if max_signals_per_day is not None:
        ranked_rows: list[dict[str, Any]] = []
        daily_counts: dict[date, int] = {}
        for row in rows:
            signal_date = row["signal_date"]
            count = daily_counts.get(signal_date, 0)
            if count >= max_signals_per_day:
                continue
            ranked_rows.append(row)
            daily_counts[signal_date] = count + 1
        rows = ranked_rows

    if not rows:
        return pl.DataFrame(schema=MR_V2_SIGNAL_SCHEMA)
    return pl.DataFrame(rows, schema=MR_V2_SIGNAL_SCHEMA).sort(
        by=["signal_date", entry_indicator, "symbol"],
        descending=[False, False, False],
    )
