from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query

from sepa_scanner.api.deps import get_duckdb_store, get_settings, get_parquet_store
from sepa_scanner.settings import AppSettings
from sepa_scanner.storage.duckdb_store import DuckDbStore
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore
from sepa_scanner.indicators.contraction import _count_contractions

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _serialize_dates(row: dict) -> dict:
    """Convert date/datetime objects in a dict to ISO strings."""
    out = {}
    for key, value in row.items():
        if isinstance(value, (date, datetime)):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _get_contraction_sequence(
    symbol: str,
    scan_date: date,
    parquet_store: ParquetOhlcvStore,
    settings: AppSettings,
) -> tuple[list[float], list[str], list[float], list[float], list[dict]]:
    start_date = scan_date - timedelta(days=150)
    df = parquet_store.read_frame(
        start_date=start_date,
        end_date=scan_date,
        columns=["date", "symbol", "close", "high", "low"],
        symbols=symbol,
    ).sort("date")
    
    if len(df) < settings.setup_vcp.base_lookback + 10:
        return [], [], [], [], []
        
    df = df.with_columns(
        (
            (
                pl.col("high").rolling_max(window_size=10, min_samples=10)
                - pl.col("low").rolling_min(window_size=10, min_samples=10)
            )
            / pl.col("close")
        ).alias("range10"),
        pl.col("high").rolling_max(window_size=10, min_samples=10).alias("high10"),
        pl.col("low").rolling_min(window_size=10, min_samples=10).alias("low10"),
    )
    
    base_lookback = settings.setup_vcp.base_lookback
    df_tail = df.tail(base_lookback)
    range10_vals = df_tail["range10"].to_numpy()
    range10_vals = np.nan_to_num(range10_vals, nan=0.0)
    
    smooth_window = settings.setup_vcp.contraction_smooth_window
    min_peak_pct = settings.setup_vcp.contraction_min_peak_pct
    
    c, q, prog, seq = _count_contractions(
        range10_vals,
        smooth_window=smooth_window,
        min_peak_pct=min_peak_pct,
    )
    
    # Calculate peak dates to align with UI markers
    smoothed_valid = np.convolve(range10_vals, np.ones(smooth_window)/smooth_window, mode="valid")
    diff = np.diff(smoothed_valid)
    peaks: list[tuple[int, float]] = []
    for idx in range(1, len(diff)):
        if diff[idx - 1] > 0 and diff[idx] <= 0:
            peak_val = smoothed_valid[idx]
            if peak_val < min_peak_pct:
                continue
            left_trough = np.min(smoothed_valid[max(0, idx - smooth_window) : idx])
            right_trough = np.min(smoothed_valid[idx : min(len(smoothed_valid), idx + smooth_window + 1)])
            prominence = peak_val - max(left_trough, right_trough)
            if prominence > min_peak_pct * 0.25:
                peaks.append((idx, peak_val))
                
    chain_peaks = peaks[len(peaks) - c:]
    peak_dates = []
    highs = []
    lows = []
    swings = []
    df_dates = df_tail["date"].to_list()
    df_highs = df_tail["high10"].to_list()
    df_lows = df_tail["low10"].to_list()
    
    raw_highs = df["high"].to_list()
    raw_lows = df["low"].to_list()
    raw_dates = df["date"].to_list()
    offset = len(df) - base_lookback

    # Build the list of peak indices in the full dataframe
    peak_indices = []
    for idx, _ in chain_peaks:
        raw_idx = idx + (smooth_window - 1) // 2
        peak_indices.append(offset + raw_idx)
        
    for i in range(len(peak_indices)):
        peak_idx = peak_indices[i]
        raw_idx = peak_idx - offset
        
        # Determine the logical boundary for swing high search
        if i == 0:
            search_start_high = max(0, peak_idx - 30)
        else:
            search_start_high = peak_indices[i - 1]
        search_end_high = min(len(df) - 1, peak_idx + 2)
        
        high_idx = search_start_high
        for j in range(search_start_high, search_end_high + 1):
            if raw_highs[j] > raw_highs[high_idx]:
                high_idx = j
                
        # Determine the logical boundary for swing low search
        search_start_low = max(0, peak_idx - 2)
        if i == len(peak_indices) - 1:
            search_end_low = len(df) - 1
        else:
            search_end_low = peak_indices[i + 1]
            
        low_idx = search_start_low
        for j in range(search_start_low, search_end_low + 1):
            if raw_lows[j] < raw_lows[low_idx]:
                low_idx = j
                
        # Handle cases where low is detected before the high
        if low_idx < high_idx:
            search_start_low_adj = high_idx
            low_idx = search_start_low_adj
            for j in range(search_start_low_adj, search_end_low + 1):
                if raw_lows[j] < raw_lows[low_idx]:
                    low_idx = j

        peak_dates.append(raw_dates[peak_idx].isoformat())
        highs.append(float(df_highs[raw_idx]) if df_highs[raw_idx] is not None else 0.0)
        lows.append(float(df_lows[raw_idx]) if df_lows[raw_idx] is not None else 0.0)
        
        swings.append({
            "date": raw_dates[high_idx].isoformat(),
            "price": float(raw_highs[high_idx]),
            "type": "high"
        })
        swings.append({
            "date": raw_dates[low_idx].isoformat(),
            "price": float(raw_lows[low_idx]),
            "type": "low"
        })
        
    return seq, peak_dates, highs, lows, swings


@router.get("/{symbol}/diagnostics")
def get_diagnostics(
    symbol: str,
    date: date = Query(..., alias="date"),
    store: DuckDbStore = Depends(get_duckdb_store),
    parquet_store: ParquetOhlcvStore = Depends(get_parquet_store),
    settings: AppSettings = Depends(get_settings),
) -> dict:
    df = store.fetch_daily_diagnostics(date)
    filtered = df.filter(pl.col("symbol") == symbol)
    if filtered.is_empty():
        raise HTTPException(status_code=404, detail=f"No diagnostics for {symbol} on {date}")
    row = filtered.to_dicts()[0]
    
    try:
        seq, peak_dates, highs, lows, swings = _get_contraction_sequence(symbol, date, parquet_store, settings)
        row["contraction_sequence"] = seq
        row["contraction_dates"] = peak_dates
        row["contraction_highs"] = highs
        row["contraction_lows"] = lows
        row["contraction_swings"] = swings
    except Exception:
        row["contraction_sequence"] = []
        row["contraction_dates"] = []
        row["contraction_highs"] = []
        row["contraction_lows"] = []
        row["contraction_swings"] = []

    return _serialize_dates(row)


def _num(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _fmt_price(value: Any) -> str:
    num = _num(value)
    if num >= 100:
        return f"₹{num:,.0f}"
    return f"₹{num:,.2f}"


def _fmt_pct(value: Any, decimals: int = 1) -> str:
    return f"{_num(value) * 100:.{decimals}f}%"


def _fmt_ratio(value: Any) -> str:
    return f"{_num(value):.2f}x"


def _metric(label: str, value: str, context: str) -> dict:
    return {"label": label, "value": value, "context": context}


def _item(text: str, detail: str, severity: str = "info") -> dict:
    return {"text": text, "detail": detail, "severity": severity}


def _signal_row(store: DuckDbStore, symbol: str, target_date: date) -> dict:
    signals = [row for row in store.fetch_signals(target_date) if row.get("symbol") == symbol]
    if signals:
        return signals[0]

    watchlist = [
        row
        for row in store.fetch_watchlist_candidates(target_date)
        if row.get("symbol") == symbol
    ]
    if watchlist:
        row = watchlist[0]
        return {
            "signal_date": row.get("watch_date"),
            "symbol": row.get("symbol"),
            "signal_type": "WATCH",
            "close": row.get("close"),
            "pivot": row.get("pivot"),
            "near_pivot": row.get("near_pivot"),
            "rs_rank": row.get("rs_rank"),
            "range10": row.get("range10"),
            "voldry": row.get("voldry"),
            "atr_pct": row.get("atr_pct"),
            "adv50": row.get("adv50"),
            "vol50": row.get("vol50"),
            "vol_mult": 0,
            "entry_ref": None,
            "stop": None,
            "r_per_share": None,
        }

    raise HTTPException(status_code=404, detail=f"No signal/watchlist row for {symbol} on {target_date}")


def _build_setup_explanation(
    *,
    symbol: str,
    target_date: date,
    signal: dict,
    diagnostics: dict,
    settings: AppSettings,
    market_filter_on: bool | None,
) -> dict:
    signal_type = str(signal.get("signal_type") or "WATCH")
    close = _num(signal.get("close"))
    pivot = _num(signal.get("pivot"))
    near_pivot = _num(signal.get("near_pivot"))
    rs_rank = _num(signal.get("rs_rank"))
    range10 = _num(signal.get("range10"))
    voldry = _num(signal.get("voldry"))
    atr_pct = _num(signal.get("atr_pct"))
    vol_mult = _num(signal.get("vol_mult"))
    adv50 = _num(signal.get("adv50"))
    entry_ref = _num(signal.get("entry_ref"))
    stop = _num(signal.get("stop"))
    risk_pct = ((entry_ref - stop) / entry_ref) if entry_ref and stop else None
    setup_streak = int(_num(diagnostics.get("setup_active_streak")))
    pivot_age = int(_num(diagnostics.get("pivot_age_days")))
    contraction_count = int(_num(diagnostics.get("contraction_count")))
    contraction_quality = _num(diagnostics.get("contraction_quality"), default=-1.0)

    rs_floor = settings.relative_strength.rs_rank_min
    near_limit = settings.setup_vcp.pre_alert_proximity_pct
    range_limit = settings.setup_vcp.max_range10_pct
    voldry_limit = settings.setup_vcp.max_vol_dryup_ratio
    breakout_vol_min = settings.breakout.breakout_vol_mult
    breakout_level = pivot * (1 + settings.breakout.breakout_buffer_pct)

    strengths: list[dict] = []
    risks: list[dict] = []
    next_actions: list[dict] = []

    if rs_rank >= 85:
        strengths.append(_item(
            f"Leadership RS: {rs_rank:.0f}",
            f"Above the {rs_floor:.0f} minimum and near the top of the current candidate set.",
        ))
    elif rs_rank >= 70:
        strengths.append(_item(
            f"Good RS: {rs_rank:.0f}",
            f"Passes the {rs_floor:.0f} RS floor, but is not top-tier leadership.",
        ))
    else:
        risks.append(_item(
            f"Middling RS: {rs_rank:.0f}",
            "It qualifies, but there are stronger relative-strength candidates today.",
            "warning",
        ))

    if diagnostics.get("tt_pass"):
        strengths.append(_item(
            "Stage 2 trend confirmed",
            "All trend-template checks passed: price and moving averages are aligned upward.",
        ))

    if near_pivot <= near_limit * 0.5:
        strengths.append(_item(
            f"Close to pivot: {_fmt_pct(near_pivot)} below",
            f"Inside the {_fmt_pct(near_limit)} watch threshold, so it is actionable to monitor.",
        ))
    elif near_pivot <= near_limit:
        risks.append(_item(
            f"Still {_fmt_pct(near_pivot)} below pivot",
            f"It is valid, but near the outer half of the {_fmt_pct(near_limit)} watch zone.",
            "warning",
        ))

    contraction_seq = diagnostics.get("contraction_sequence", [])
    seq_str = f" ({' ➔ '.join(f'{v:.1f}%' for v in contraction_seq)})" if contraction_seq else ""
    if contraction_count >= 2:
        quality = (
            f" with {contraction_quality * 100:.0f}% quality"
            if contraction_quality >= 0
            else ""
        )
        strengths.append(_item(
            f"{contraction_count} contractions detected{seq_str}",
            f"Shows repeated tightening in the base{quality}.",
        ))
    else:
        risks.append(_item(
            f"Only {contraction_count} contraction detected{seq_str}",
            "The setup passes, but VCP structure is not strongly layered.",
            "warning",
        ))

    if range10 <= range_limit * 0.75:
        strengths.append(_item(
            f"Tight range: {_fmt_pct(range10)}",
            f"Comfortably below the {_fmt_pct(range_limit)} maximum.",
        ))
    elif range10 <= range_limit:
        risks.append(_item(
            f"Range is near limit: {_fmt_pct(range10)}",
            f"Threshold is {_fmt_pct(range_limit)}; volatility compression is only just acceptable.",
            "warning",
        ))

    if voldry <= 1.0:
        strengths.append(_item(
            f"Supply quiet: volume dry-up {_fmt_ratio(voldry)}",
            "Volume is subdued inside the base, which is what a watch setup wants.",
        ))
    elif voldry > 0:
        risks.append(_item(
            f"Volume dry-up is not clean: {_fmt_ratio(voldry)}",
            f"It passes the loose {_fmt_ratio(voldry_limit)} cap, but supply is not especially quiet.",
            "warning",
        ))

    if signal_type == "WATCH" and vol_mult < 1.0:
        risks.append(_item(
            f"No demand confirmation yet: {_fmt_ratio(vol_mult)} volume",
            "Fine for a watch signal, but not enough for a breakout entry.",
            "warning",
        ))
    elif vol_mult >= 1.5:
        strengths.append(_item(
            f"Volume interest: {_fmt_ratio(vol_mult)}",
            "Today's volume is meaningfully above the 50-day average.",
        ))

    if setup_streak >= 5:
        risks.append(_item(
            f"Setup is getting stale: {setup_streak} days active",
            "Fresh setups are preferred; stale watches can fail or drift sideways.",
            "warning",
        ))
    elif setup_streak > 0:
        strengths.append(_item(
            f"Fresh setup: {setup_streak} days active",
            "Recent enough to watch without treating it as overcooked.",
        ))

    if pivot_age >= 5:
        risks.append(_item(
            f"Pivot is {pivot_age} days old",
            "Older pivots can become obvious and attract failed breakouts.",
            "warning",
        ))

    if risk_pct is not None:
        if risk_pct > settings.risk.max_stop_pct * 0.9:
            risks.append(_item(
                f"Wide initial risk: {risk_pct * 100:.1f}%",
                f"Close to the configured {settings.risk.max_stop_pct * 100:.0f}% maximum stop cap.",
                "warning",
            ))
        else:
            strengths.append(_item(
                f"Defined risk: {risk_pct * 100:.1f}%",
                f"Entry reference {_fmt_price(entry_ref)} with stop {_fmt_price(stop)}.",
            ))

    if market_filter_on is False:
        risks.append(_item(
            "Market filter is off",
            "The individual setup qualifies, but the broad market regime is not supportive.",
            "warning",
        ))

    if signal_type == "BREAKOUT":
        next_actions.append(_item(
            "Review entry quality now",
            f"Breakout signal triggered above pivot {_fmt_price(pivot)}.",
        ))
        next_actions.append(_item(
            "Use the planned stop",
            f"Risk is invalidated below {_fmt_price(stop)}.",
        ))
    else:
        next_actions.append(_item(
            "Wait for breakout confirmation",
            f"Look for a close above roughly {_fmt_price(breakout_level)} with volume >= {_fmt_ratio(breakout_vol_min)} 50D average.",
        ))
        next_actions.append(_item(
            "Do not chase",
            f"If it runs far beyond the pivot without a clean signal, leave it alone.",
        ))

    if not risks:
        risks.append(_item(
            "No major rule-level warning",
            "Still require breakout confirmation and normal position sizing.",
        ))

    narrative = (
        f"{symbol} is a {signal_type.lower()} candidate for {target_date.isoformat()}: "
        f"RS {rs_rank:.0f}, {_fmt_pct(near_pivot)} below pivot {_fmt_price(pivot)}, "
        f"range10 {_fmt_pct(range10)}, volume {_fmt_ratio(vol_mult)}. "
    )
    if signal_type == "WATCH":
        narrative += "It is not an entry yet; it is a watchlist candidate waiting for price and volume confirmation."
    else:
        narrative += "It has triggered a breakout signal; the remaining question is execution quality and risk."

    metrics = [
        _metric("RS rank", f"{rs_rank:.0f}", f"minimum {rs_floor:.0f}"),
        _metric("Distance to pivot", _fmt_pct(near_pivot), f"watch max {_fmt_pct(near_limit)}"),
        _metric("Volume", _fmt_ratio(vol_mult), "vs 50D average"),
        _metric("Range10", _fmt_pct(range10), f"max {_fmt_pct(range_limit)}"),
        _metric("Volume dry-up", _fmt_ratio(voldry), f"max {_fmt_ratio(voldry_limit)}"),
        _metric("ATR", _fmt_pct(atr_pct), "current ATR / close"),
        _metric("ADV50", f"₹{adv50 / 1e7:.0f}Cr", f"min ₹{settings.universe.min_adv50_inr / 1e7:.0f}Cr"),
    ]

    return {
        "symbol": symbol,
        "date": target_date,
        "signal_type": signal_type,
        "narrative": narrative,
        "strengths": strengths[:6],
        "risks": risks[:6],
        "next_actions": next_actions,
        "metrics": metrics,
    }


@router.get("/{symbol}/setup-explanation")
def get_setup_explanation(
    symbol: str,
    date: date = Query(..., alias="date"),
    store: DuckDbStore = Depends(get_duckdb_store),
    parquet_store: ParquetOhlcvStore = Depends(get_parquet_store),
    settings: AppSettings = Depends(get_settings),
) -> dict:
    df = store.fetch_daily_diagnostics(date)
    filtered = df.filter(pl.col("symbol") == symbol)
    if filtered.is_empty():
        raise HTTPException(status_code=404, detail=f"No diagnostics for {symbol} on {date}")

    diagnostics = filtered.to_dicts()[0]
    try:
        seq, peak_dates, highs, lows, swings = _get_contraction_sequence(symbol, date, parquet_store, settings)
        diagnostics["contraction_sequence"] = seq
        diagnostics["contraction_dates"] = peak_dates
        diagnostics["contraction_highs"] = highs
        diagnostics["contraction_lows"] = lows
        diagnostics["contraction_swings"] = swings
    except Exception:
        diagnostics["contraction_sequence"] = []
        diagnostics["contraction_dates"] = []
        diagnostics["contraction_highs"] = []
        diagnostics["contraction_lows"] = []
        diagnostics["contraction_swings"] = []

    signal = _signal_row(store, symbol, date)
    conn = store._connect()
    market_filter_on = conn.execute(
        """
        SELECT market_filter_on
        FROM runs
        WHERE scan_date = ? AND status = 'success'
        ORDER BY started_at DESC
        LIMIT 1
        """,
        [date],
    ).fetchone()
    explanation = _build_setup_explanation(
        symbol=symbol,
        target_date=date,
        signal=signal,
        diagnostics=diagnostics,
        settings=settings,
        market_filter_on=market_filter_on[0] if market_filter_on else None,
    )
    explanation["contraction_sequence"] = diagnostics["contraction_sequence"]
    explanation["contraction_dates"] = diagnostics["contraction_dates"]
    explanation["contraction_highs"] = diagnostics["contraction_highs"]
    explanation["contraction_lows"] = diagnostics["contraction_lows"]
    explanation["contraction_swings"] = diagnostics["contraction_swings"]
    return _serialize_dates(explanation)


@router.get("/{symbol}/signal-history")
def get_signal_history(
    symbol: str,
    store: DuckDbStore = Depends(get_duckdb_store),
    settings: AppSettings = Depends(get_settings),
) -> dict:
    conn = store._connect()
    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE symbol = ?
        ORDER BY signal_date DESC
        """,
        [symbol],
    ).fetchall()
    columns = [desc[0] for desc in conn.description]
    serialized_signals = [_serialize_dates(dict(zip(columns, row, strict=False))) for row in rows]

    cache_path: Path = settings.runtime.data_dir / "backtest" / "latest.json"
    filtered_trades: list[dict] = []
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        filtered_trades = [t for t in cached.get("trades", []) if t.get("symbol") == symbol]

    return {"signals": serialized_signals, "trades": filtered_trades}
