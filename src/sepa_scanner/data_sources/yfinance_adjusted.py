from __future__ import annotations

import math
import logging
import time
import random
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import polars as pl
import yfinance as yf


LOGGER = logging.getLogger(__name__)


CORE_COLUMNS = [
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
]


def _split_adjust_volume(raw_volume: pd.Series, split_ratios: pd.Series) -> pd.Series:
    factor = 1.0
    adjusted: list[float] = []

    for volume, split_ratio in zip(
        reversed(raw_volume.tolist()),
        reversed(split_ratios.tolist()),
        strict=False,
    ):
        adjusted.append(float(volume) * factor)
        if pd.notna(split_ratio):
            ratio = float(split_ratio)
            if ratio not in (0.0, 1.0):
                factor *= ratio

    return pd.Series(list(reversed(adjusted)), index=raw_volume.index, dtype="float64")


def normalize_history_frame(raw: pd.DataFrame, symbol: str) -> pl.DataFrame:
    if raw.empty:
        return pl.DataFrame(schema={column: pl.String for column in CORE_COLUMNS})

    pdf = raw.copy()
    pdf.index = pd.to_datetime(pdf.index)

    raw_close = pd.to_numeric(pdf["Close"], errors="coerce")
    adj_close = pd.to_numeric(pdf.get("Adj Close", pdf["Close"]), errors="coerce")
    split_ratios = pd.to_numeric(pdf.get("Stock Splits", 0.0), errors="coerce").fillna(0.0)
    raw_volume = pd.to_numeric(pdf.get("Volume", 0), errors="coerce").fillna(0.0)

    ratio = (adj_close / raw_close.replace(0.0, pd.NA)).fillna(1.0)
    adjusted_volume = _split_adjust_volume(raw_volume, split_ratios)
    turnover = (raw_close * raw_volume).fillna(0.0)

    normalized = pd.DataFrame(
        {
            "date": pdf.index.date,
            "symbol": symbol,
            "open": pd.to_numeric(pdf["Open"], errors="coerce") * ratio,
            "high": pd.to_numeric(pdf["High"], errors="coerce") * ratio,
            "low": pd.to_numeric(pdf["Low"], errors="coerce") * ratio,
            "close": adj_close,
            "volume": adjusted_volume.map(lambda value: int(round(value)) if math.isfinite(value) else 0),
            "turnover": turnover,
        }
    )
    normalized = normalized.dropna(subset=["close"])
    return pl.from_pandas(normalized).select(CORE_COLUMNS)


def _extract_download_frames(
    raw: pd.DataFrame,
    tickers: list[str],
) -> tuple[list[pl.DataFrame], list[str]]:
    frames: list[pl.DataFrame] = []
    failed: list[str] = []

    if isinstance(raw.columns, pd.MultiIndex):
        for ticker in tickers:
            symbol = ticker.removesuffix(".NS")
            try:
                subset = raw.xs(ticker, level="Ticker", axis=1)
                if subset.dropna(how="all").empty:
                    failed.append(symbol)
                    continue
                frame = normalize_history_frame(subset, symbol=symbol)
                if frame.is_empty():
                    failed.append(symbol)
                    continue
                frames.append(frame)
            except (KeyError, Exception) as exc:
                LOGGER.warning("Failed to normalize %s: %s", symbol, exc)
                failed.append(symbol)
    else:
        if len(tickers) == 1:
            symbol = tickers[0].removesuffix(".NS")
            frame = normalize_history_frame(raw, symbol=symbol)
            if frame.is_empty():
                failed.append(symbol)
            else:
                frames.append(frame)
        else:
            for ticker in tickers:
                failed.append(ticker.removesuffix(".NS"))

    return frames, failed


def _download_with_retry(
    tickers: list[str],
    start_str: str,
    end_str: str | None,
    max_retries: int = 3,
) -> pd.DataFrame:
    delay = 1.0  # Initial retry delay in seconds
    for attempt in range(max_retries):
        try:
            raw = yf.download(
                tickers,
                start=start_str,
                end=end_str,
                auto_adjust=False,
                actions=True,
                progress=False,
                threads=len(tickers) > 1,
            )
            if not raw.empty:
                return raw
        except Exception as exc:
            exc_str = str(exc)
            if "429" in exc_str or "Too Many Requests" in exc_str:
                LOGGER.warning("HTTP 429 encountered for %s. Backing off...", tickers)
            else:
                LOGGER.warning("Error downloading %s: %s", tickers, exc)

        if attempt < max_retries - 1:
            sleep_time = delay * (2 ** attempt) + random.uniform(0.1, 0.5)
            time.sleep(sleep_time)

    # One final synchronous attempt
    try:
        return yf.download(
            tickers,
            start=start_str,
            end=end_str,
            auto_adjust=False,
            actions=True,
            progress=False,
            threads=len(tickers) > 1,
        )
    except Exception:
        return pd.DataFrame()


def _download_worker(
    batch: list[str],
    start_str: str,
    end_str: str | None,
) -> tuple[list[pl.DataFrame], list[str]]:
    # Sleep 100-500ms between batches to throttle requests and avoid rate limits
    time.sleep(random.uniform(0.1, 0.5))

    raw = _download_with_retry(batch, start_str, end_str)
    if raw.empty:
        return [], [t.removesuffix(".NS") for t in batch]

    frames, failed = _extract_download_frames(raw, batch)
    return frames, failed


def download_adjusted_history(
    symbols: list[str],
    *,
    start: date,
    end: date | None = None,
    batch_size: int = 100,
) -> tuple[pl.DataFrame, list[str]]:
    tickers = [f"{symbol}.NS" for symbol in sorted(set(symbols))]
    if not tickers:
        return pl.DataFrame(schema={column: pl.String for column in CORE_COLUMNS}), []

    all_frames: list[pl.DataFrame] = []
    all_failed: list[str] = []
    start_str = start.isoformat()
    end_str = end.isoformat() if end is not None else None

    # Group tickers into chunks of 100 (batch_size defaults to 100 now)
    batches = [tickers[i : i + batch_size] for i in range(0, len(tickers), batch_size)]

    # Use ThreadPoolExecutor to download batches concurrently
    max_workers = min(4, len(batches)) if batches else 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_download_worker, batch, start_str, end_str): batch
            for batch in batches
        }
        for future in as_completed(futures):
            try:
                frames, failed = future.result()
                all_frames.extend(frames)
                all_failed.extend(failed)
            except Exception as exc:
                batch = futures[future]
                LOGGER.error("Batch download failed in thread pool for %s: %s", batch, exc)
                all_failed.extend(t.removesuffix(".NS") for t in batch)

    # Retry failed symbols individually with exponential backoff retry logic
    if all_failed:
        LOGGER.info("Retrying %d failed symbols individually...", len(all_failed))
        retried_failed: list[str] = []
        max_retry_workers = min(2, len(all_failed))
        with ThreadPoolExecutor(max_workers=max_retry_workers) as executor:
            futures = {
                executor.submit(_download_worker, [f"{symbol}.NS"], start_str, end_str): symbol
                for symbol in all_failed
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    frames, failed = future.result()
                    if frames:
                        all_frames.extend(frames)
                    if failed:
                        retried_failed.extend(failed)
                except Exception as exc:
                    LOGGER.error("Retry worker failed for %s: %s", symbol, exc)
                    retried_failed.append(symbol)
        all_failed = retried_failed

    if not all_frames:
        return pl.DataFrame(schema={column: pl.String for column in CORE_COLUMNS}), all_failed

    combined = (
        pl.concat(all_frames)
        .unique(subset=["date", "symbol"], keep="last")
        .sort(["symbol", "date"])
    )
    return combined, all_failed
