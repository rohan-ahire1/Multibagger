from __future__ import annotations

import math
from collections.abc import Sequence


def _nan_list(length: int) -> list[float]:
    return [math.nan] * length


def _is_missing(value: float | int | None) -> bool:
    return value is None or math.isnan(float(value))


def sma(values: Sequence[float | int | None], period: int) -> list[float]:
    """Simple moving average. NaN-filled for warmup."""
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return []

    result = _nan_list(len(values))
    window_sum = 0.0
    valid_count = 0
    window: list[float | None] = []

    for idx, raw in enumerate(values):
        value = None if _is_missing(raw) else float(raw)
        window.append(value)
        if value is not None:
            window_sum += value
            valid_count += 1
        if len(window) > period:
            old = window.pop(0)
            if old is not None:
                window_sum -= old
                valid_count -= 1
        if len(window) == period and valid_count == period:
            result[idx] = window_sum / period
    return result


def rsi(closes: Sequence[float | int | None], period: int = 2) -> list[float]:
    """Wilder RSI. Returns list same length as closes, NaN-filled for warmup."""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(closes) == 0:
        return []

    result = _nan_list(len(closes))
    clean = [math.nan if _is_missing(value) else float(value) for value in closes]
    if len(clean) <= period:
        return result

    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, period + 1):
        if math.isnan(clean[idx]) or math.isnan(clean[idx - 1]):
            return result
        delta = clean[idx] - clean[idx - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    result[period] = _rsi_from_avgs(avg_gain, avg_loss)

    for idx in range(period + 1, len(clean)):
        if math.isnan(clean[idx]) or math.isnan(clean[idx - 1]):
            avg_gain = math.nan
            avg_loss = math.nan
            continue
        delta = clean[idx] - clean[idx - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        if math.isnan(avg_gain) or math.isnan(avg_loss):
            start = idx - period + 1
            deltas = [clean[j] - clean[j - 1] for j in range(start, idx + 1)]
            avg_gain = sum(max(delta, 0.0) for delta in deltas) / period
            avg_loss = sum(max(-delta, 0.0) for delta in deltas) / period
        else:
            avg_gain = ((avg_gain * (period - 1)) + gain) / period
            avg_loss = ((avg_loss * (period - 1)) + loss) / period
        result[idx] = _rsi_from_avgs(avg_gain, avg_loss)

    return result


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    if avg_gain == 0.0 and avg_loss == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    if avg_gain == 0.0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(
    highs: Sequence[float | int | None],
    lows: Sequence[float | int | None],
    closes: Sequence[float | int | None],
    period: int = 14,
) -> list[float]:
    """Average True Range using Wilder smoothing."""
    if period <= 0:
        raise ValueError("period must be positive")
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows, and closes must have the same length")
    if not closes:
        return []

    result = _nan_list(len(closes))
    true_ranges: list[float] = []
    for idx, (high_raw, low_raw, close_raw) in enumerate(zip(highs, lows, closes, strict=True)):
        if _is_missing(high_raw) or _is_missing(low_raw) or _is_missing(close_raw):
            true_ranges.append(math.nan)
            continue
        high = float(high_raw)
        low = float(low_raw)
        if idx == 0 or _is_missing(closes[idx - 1]):
            true_ranges.append(high - low)
        else:
            prev_close = float(closes[idx - 1])
            true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    if len(true_ranges) <= period:
        return result
    seed = true_ranges[1 : period + 1]
    if any(math.isnan(value) for value in seed):
        return result
    avg_atr = sum(seed) / period
    result[period] = avg_atr

    for idx in range(period + 1, len(true_ranges)):
        tr = true_ranges[idx]
        if math.isnan(tr):
            avg_atr = math.nan
            continue
        if math.isnan(avg_atr):
            seed = true_ranges[idx - period + 1 : idx + 1]
            if any(math.isnan(value) for value in seed):
                continue
            avg_atr = sum(seed) / period
        else:
            avg_atr = ((avg_atr * (period - 1)) + tr) / period
        result[idx] = avg_atr

    return result


def adv(
    volumes: Sequence[float | int | None],
    closes: Sequence[float | int | None],
    period: int = 50,
) -> list[float]:
    """Average daily value traded: volume * close over period."""
    if len(volumes) != len(closes):
        raise ValueError("volumes and closes must have the same length")
    values = [
        math.nan if _is_missing(volume) or _is_missing(close) else float(volume) * float(close)
        for volume, close in zip(volumes, closes, strict=True)
    ]
    return sma(values, period)


def streak(closes: Sequence[float | int | None]) -> list[float]:
    """
    Consecutive up/down close counter.
    First bar is NaN. Missing bars reset the running streak.
    """
    if not closes:
        return []

    result = _nan_list(len(closes))
    clean = [math.nan if _is_missing(value) else float(value) for value in closes]
    prev_streak = 0.0
    for idx in range(1, len(clean)):
        if math.isnan(clean[idx]) or math.isnan(clean[idx - 1]):
            prev_streak = 0.0
            continue
        if clean[idx] > clean[idx - 1]:
            prev_streak = prev_streak + 1.0 if prev_streak > 0.0 else 1.0
        elif clean[idx] < clean[idx - 1]:
            prev_streak = prev_streak - 1.0 if prev_streak < 0.0 else -1.0
        else:
            prev_streak = 0.0
        result[idx] = prev_streak
    return result


def percent_rank(values: Sequence[float | int | None], period: int = 100) -> list[float]:
    """
    Percent rank of the current value against the previous `period` values.
    Uses a strict less-than comparison and returns 0-100.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return []

    result = _nan_list(len(values))
    clean = [math.nan if _is_missing(value) else float(value) for value in values]
    for idx in range(period, len(clean)):
        current = clean[idx]
        window = clean[idx - period : idx]
        if math.isnan(current) or any(math.isnan(value) for value in window):
            continue
        result[idx] = (sum(1 for value in window if value < current) / period) * 100.0
    return result


def ibs(
    highs: Sequence[float | int | None],
    lows: Sequence[float | int | None],
    closes: Sequence[float | int | None],
) -> list[float]:
    """Internal Bar Strength: (close - low) / (high - low)."""
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows, and closes must have the same length")

    result = _nan_list(len(closes))
    for idx, (high_raw, low_raw, close_raw) in enumerate(zip(highs, lows, closes, strict=True)):
        if _is_missing(high_raw) or _is_missing(low_raw) or _is_missing(close_raw):
            continue
        high = float(high_raw)
        low = float(low_raw)
        close = float(close_raw)
        if high == low:
            result[idx] = 0.5
        else:
            result[idx] = max(0.0, min(1.0, (close - low) / (high - low)))
    return result


def adx(
    highs: Sequence[float | int | None],
    lows: Sequence[float | int | None],
    closes: Sequence[float | int | None],
    period: int = 14,
) -> list[float]:
    """Wilder Average Directional Index."""
    if period <= 0:
        raise ValueError("period must be positive")
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows, and closes must have the same length")
    if not closes:
        return []

    result = _nan_list(len(closes))
    plus_dm = _nan_list(len(closes))
    minus_dm = _nan_list(len(closes))
    true_ranges = _nan_list(len(closes))
    clean_highs = [math.nan if _is_missing(value) else float(value) for value in highs]
    clean_lows = [math.nan if _is_missing(value) else float(value) for value in lows]
    clean_closes = [math.nan if _is_missing(value) else float(value) for value in closes]

    for idx in range(1, len(closes)):
        if any(
            math.isnan(value)
            for value in (
                clean_highs[idx],
                clean_lows[idx],
                clean_closes[idx],
                clean_highs[idx - 1],
                clean_lows[idx - 1],
                clean_closes[idx - 1],
            )
        ):
            continue
        up_move = clean_highs[idx] - clean_highs[idx - 1]
        down_move = clean_lows[idx - 1] - clean_lows[idx]
        plus_dm[idx] = up_move if up_move > 0.0 and up_move > down_move else 0.0
        minus_dm[idx] = down_move if down_move > 0.0 and down_move > up_move else 0.0
        true_ranges[idx] = max(
            clean_highs[idx] - clean_lows[idx],
            abs(clean_highs[idx] - clean_closes[idx - 1]),
            abs(clean_lows[idx] - clean_closes[idx - 1]),
        )

    if len(closes) <= period * 2:
        return result

    seed_slice = slice(1, period + 1)
    if any(math.isnan(value) for value in true_ranges[seed_slice] + plus_dm[seed_slice] + minus_dm[seed_slice]):
        return result

    smoothed_tr = sum(true_ranges[seed_slice])
    smoothed_plus = sum(plus_dm[seed_slice])
    smoothed_minus = sum(minus_dm[seed_slice])
    dx_values = _nan_list(len(closes))

    for idx in range(period, len(closes)):
        if idx > period:
            if any(math.isnan(value) for value in (true_ranges[idx], plus_dm[idx], minus_dm[idx])):
                smoothed_tr = math.nan
                smoothed_plus = math.nan
                smoothed_minus = math.nan
                continue
            if math.isnan(smoothed_tr) or math.isnan(smoothed_plus) or math.isnan(smoothed_minus):
                seed_start = idx - period + 1
                tr_seed = true_ranges[seed_start : idx + 1]
                plus_seed = plus_dm[seed_start : idx + 1]
                minus_seed = minus_dm[seed_start : idx + 1]
                if any(math.isnan(value) for value in tr_seed + plus_seed + minus_seed):
                    continue
                smoothed_tr = sum(tr_seed)
                smoothed_plus = sum(plus_seed)
                smoothed_minus = sum(minus_seed)
            else:
                smoothed_tr = smoothed_tr - (smoothed_tr / period) + true_ranges[idx]
                smoothed_plus = smoothed_plus - (smoothed_plus / period) + plus_dm[idx]
                smoothed_minus = smoothed_minus - (smoothed_minus / period) + minus_dm[idx]

        if math.isnan(smoothed_tr):
            continue
        if smoothed_tr == 0.0:
            dx_values[idx] = 0.0
        else:
            plus_di = (smoothed_plus / smoothed_tr) * 100.0
            minus_di = (smoothed_minus / smoothed_tr) * 100.0
            denom = plus_di + minus_di
            dx_values[idx] = 0.0 if denom == 0.0 else (abs(plus_di - minus_di) / denom) * 100.0

    adx_seed = dx_values[period : (period * 2)]
    if any(math.isnan(value) for value in adx_seed):
        return result
    current_adx = sum(adx_seed) / period
    result[(period * 2) - 1] = current_adx

    for idx in range(period * 2, len(closes)):
        dx = dx_values[idx]
        if math.isnan(dx):
            current_adx = math.nan
            continue
        if math.isnan(current_adx):
            seed = dx_values[idx - period + 1 : idx + 1]
            if any(math.isnan(value) for value in seed):
                continue
            current_adx = sum(seed) / period
        else:
            current_adx = ((current_adx * (period - 1)) + dx) / period
        result[idx] = current_adx
    return result


def cumulative_rsi(
    closes: Sequence[float | int | None],
    rsi_period: int = 2,
    sum_period: int = 2,
) -> list[float]:
    """Sum of the last `sum_period` RSI values."""
    if sum_period <= 0:
        raise ValueError("sum_period must be positive")
    rsi_values = rsi(closes, rsi_period)
    result = _nan_list(len(rsi_values))
    for idx in range(sum_period - 1, len(rsi_values)):
        window = rsi_values[idx - sum_period + 1 : idx + 1]
        if any(math.isnan(value) for value in window):
            continue
        result[idx] = sum(window)
    return result


def volume_percent_rank(
    volumes: Sequence[float | int | None],
    period: int = 20,
) -> list[float]:
    """Percent rank of today's volume versus the last `period` days."""
    return percent_rank(volumes, period)


def days_down(closes: Sequence[float | int | None], lookback: int = 5) -> list[float]:
    """Count down closes in the last `lookback` close-to-close comparisons."""
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if not closes:
        return []

    result = _nan_list(len(closes))
    clean = [math.nan if _is_missing(value) else float(value) for value in closes]
    for idx in range(lookback, len(clean)):
        count = 0
        valid = True
        for j in range(idx - lookback + 1, idx + 1):
            if math.isnan(clean[j]) or math.isnan(clean[j - 1]):
                valid = False
                break
            if clean[j] < clean[j - 1]:
                count += 1
        if valid:
            result[idx] = float(count)
    return result


def connors_rsi(
    closes: Sequence[float | int | None],
    rsi_period: int = 3,
    streak_rsi_period: int = 2,
    pct_rank_period: int = 100,
) -> list[float]:
    """ConnorsRSI = average of RSI(close), RSI(streak), and PercentRank(ROC1)."""
    if not closes:
        return []

    clean = [math.nan if _is_missing(value) else float(value) for value in closes]
    close_rsi = rsi(clean, rsi_period)
    streak_values = streak(clean)
    if streak_values:
        streak_values[0] = 0.0
    streak_rsi = rsi(streak_values, streak_rsi_period)
    roc1 = _nan_list(len(clean))
    for idx in range(1, len(clean)):
        if math.isnan(clean[idx]) or math.isnan(clean[idx - 1]) or clean[idx - 1] == 0.0:
            continue
        roc1[idx] = (clean[idx] - clean[idx - 1]) / clean[idx - 1]
    roc_rank = percent_rank(roc1, pct_rank_period)

    result = _nan_list(len(clean))
    for idx, (close_value, streak_value, rank_value) in enumerate(zip(close_rsi, streak_rsi, roc_rank, strict=True)):
        if any(math.isnan(value) for value in (close_value, streak_value, rank_value)):
            continue
        result[idx] = (close_value + streak_value + rank_value) / 3.0
    return result
