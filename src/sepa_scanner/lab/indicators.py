"""Pure indicators used by the Stocks on the Move portfolio strategy."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def _array(values: Sequence[float | None] | np.ndarray) -> np.ndarray:
    return np.asarray(values, dtype=float)


def sma(values: Sequence[float | None] | np.ndarray, window: int) -> np.ndarray:
    """Simple moving average, with ``nan`` until a complete window exists."""
    if window <= 0:
        raise ValueError("window must be positive")
    arr = _array(values)
    out = np.full(arr.size, np.nan)
    if arr.size < window:
        return out
    for end in range(window, arr.size + 1):
        sample = arr[end - window : end]
        if np.isfinite(sample).all():
            out[end - 1] = sample.mean()
    return out


def true_range(
    high: Sequence[float | None] | np.ndarray,
    low: Sequence[float | None] | np.ndarray,
    prev_close: Sequence[float | None] | np.ndarray,
) -> np.ndarray:
    """True range as ``max(high-low, |high-prev close|, |low-prev close|)``."""
    hi, lo, prev = _array(high), _array(low), _array(prev_close)
    if not (hi.size == lo.size == prev.size):
        raise ValueError("high, low, and prev_close must have equal length")
    return np.maximum.reduce((hi - lo, np.abs(hi - prev), np.abs(lo - prev)))


def _tr_from_ohlc(ohlc: object) -> np.ndarray:
    if hasattr(ohlc, "get_column"):
        high = ohlc.get_column("high").to_numpy()
        low = ohlc.get_column("low").to_numpy()
        close = ohlc.get_column("close").to_numpy()
    elif isinstance(ohlc, dict):
        high, low, close = ohlc["high"], ohlc["low"], ohlc["close"]
    else:
        high, low, close = ohlc.high, ohlc.low, ohlc.close
    close_arr = _array(close)
    prev = np.roll(close_arr, 1)
    prev[0] = close_arr[0]
    return true_range(high, low, prev)


def atr_simple(ohlc: object, window: int = 20) -> np.ndarray:
    """Arithmetic mean true range; the book-faithful ATR baseline."""
    return sma(_tr_from_ohlc(ohlc), window)


def atr_wilder(ohlc: object, window: int = 20) -> np.ndarray:
    """Wilder-smoothed ATR, retained as the documented ambiguity variant."""
    if window <= 0:
        raise ValueError("window must be positive")
    tr = _tr_from_ohlc(ohlc)
    out = np.full(tr.size, np.nan)
    if tr.size < window:
        return out
    initial = tr[:window]
    if np.isfinite(initial).all():
        out[window - 1] = initial.mean()
    for i in range(window, tr.size):
        if np.isfinite(out[i - 1]) and np.isfinite(tr[i]):
            out[i] = (out[i - 1] * (window - 1) + tr[i]) / window
    return out


def log_regression_slope_r2(
    prices: Sequence[float | None] | np.ndarray, annualisation: int = 250
) -> tuple[float, float] | None:
    """OLS log-price slope converted to annualised exponential growth and R²."""
    values = _array(prices)
    if values.size < 2 or not np.isfinite(values).all():
        return None
    if (values <= 0).any():
        raise ValueError("log regression requires strictly positive prices")
    x = np.arange(values.size, dtype=float)
    y = np.log(values)
    beta, alpha = np.polyfit(x, y, 1)
    fitted = alpha + beta * x
    ss_total = float(((y - y.mean()) ** 2).sum())
    r_squared = 1.0 if ss_total == 0 else 1.0 - float(((y - fitted) ** 2).sum()) / ss_total
    return math.exp(float(beta) * annualisation) - 1.0, max(0.0, min(1.0, r_squared))


def momentum_score(
    prices: Sequence[float | None] | np.ndarray, window: int = 90, annualisation: int = 250
) -> float | None:
    """Annualised log-regression slope times R² over the latest complete window."""
    values = _array(prices)
    if values.size < window:
        return None
    result = log_regression_slope_r2(values[-window:], annualisation)
    return None if result is None else result[0] * result[1]


def gap_close_to_close(adj_close: Sequence[float | None] | np.ndarray) -> np.ndarray:
    values = _array(adj_close)
    out = np.full(values.size, np.nan)
    if values.size > 1:
        out[1:] = np.abs(values[1:] / values[:-1] - 1.0)
    return out


def gap_open(
    raw_open: Sequence[float | None] | np.ndarray,
    raw_prev_close: Sequence[float | None] | np.ndarray,
) -> np.ndarray:
    opening, previous = _array(raw_open), _array(raw_prev_close)
    if opening.size != previous.size:
        raise ValueError("raw_open and raw_prev_close must have equal length")
    return np.abs(opening / previous - 1.0)


def max_gap(metric: Sequence[float | None] | np.ndarray, window: int = 90) -> np.ndarray:
    if window <= 0:
        raise ValueError("window must be positive")
    values = _array(metric)
    out = np.full(values.size, np.nan)
    for end in range(window, values.size + 1):
        sample = values[end - window : end]
        if np.isfinite(sample).all():
            out[end - 1] = sample.max()
    return out
