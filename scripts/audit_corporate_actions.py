from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import yfinance as yf


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit yfinance corporate action adjustments.")
    parser.add_argument("--db", type=Path, default=Path("data/db/sepa.duckdb"))
    parser.add_argument("--ohlcv", type=Path, default=Path("data/yf_adjusted/ohlcv_all.parquet"))
    parser.add_argument("--reference-dir", type=Path, default=Path("data/reference"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/corporate_actions_audit"))
    parser.add_argument("--seed-actions", type=Path, default=Path("config/corporate_actions.csv"))
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().upper().removesuffix(".NS").removesuffix(".BO")


def signal_symbols(db_path: Path) -> list[str]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT DISTINCT symbol
            FROM signals
            WHERE signal_type = 'BREAKOUT'
            ORDER BY symbol
            """
        ).fetchall()
    finally:
        con.close()
    return [normalize_symbol(row[0]) for row in rows]


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def fetch_splits(symbol: str) -> dict[str, Any]:
    splits = yf.Ticker(f"{symbol}.NS").splits
    actions = []
    if splits is not None and not splits.empty:
        for idx, ratio in splits.items():
            action_date = idx.date() if hasattr(idx, "date") else idx
            actions.append(
                {
                    "symbol": symbol,
                    "date": action_date.isoformat(),
                    "action_type": "split",
                    "ratio": float(ratio),
                    "source": "yfinance",
                }
            )
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "actions": actions,
    }


def load_signal_frame(db_path: Path) -> pl.DataFrame:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(
            """
            SELECT symbol, signal_date, entry_ref, stop, "pivot"
            FROM signals
            WHERE signal_type = 'BREAKOUT'
            ORDER BY symbol, signal_date
            """
        ).pl()
    finally:
        con.close()


def nearest_before_after(dates: list[date], action_date: date) -> tuple[int | None, int | None]:
    before = None
    after = None
    for idx, value in enumerate(dates):
        if value < action_date:
            before = idx
        if value >= action_date:
            after = idx
            break
    return before, after


def rows_to_jsonable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        item = {}
        for key, value in row.items():
            if isinstance(value, (date, datetime)):
                item[key] = value.isoformat()
            elif isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                item[key] = None
            else:
                item[key] = value
        out.append(item)
    return out


def validate_actions(actions: pl.DataFrame, ohlcv: pl.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    adjustment_rows: list[dict[str, Any]] = []
    suspect_detail_rows: list[dict[str, Any]] = []
    if actions.is_empty():
        return adjustment_rows, suspect_detail_rows

    by_symbol = {
        str(group["symbol"][0]): group.sort("date")
        for group in ohlcv.partition_by("symbol", maintain_order=True)
    }
    for action in actions.iter_rows(named=True):
        symbol = str(action["symbol"])
        action_date = action["date"]
        if isinstance(action_date, datetime):
            action_date = action_date.date()
        frame = by_symbol.get(symbol)
        if frame is None or frame.is_empty():
            adjustment_rows.append({**action, "status": "missing_ohlcv"})
            continue

        dates = frame["date"].to_list()
        before_idx, after_idx = nearest_before_after(dates, action_date)
        if before_idx is None or after_idx is None:
            adjustment_rows.append({**action, "status": "missing_before_after"})
            continue

        before_close = float(frame["close"][before_idx])
        after_close = float(frame["close"][after_idx])
        actual_ratio = before_close / after_close if after_close else None
        adjustment_error_pct = abs((actual_ratio or 0.0) - 1.0) * 100.0 if actual_ratio is not None else None
        row = {
            **action,
            "status": "validated",
            "before_date": dates[before_idx],
            "after_date": dates[after_idx],
            "before_close": before_close,
            "after_close": after_close,
            "expected_ratio": float(action["ratio"]),
            "actual_ratio": actual_ratio,
            "adjustment_error_pct": adjustment_error_pct,
            "suspect": bool(adjustment_error_pct is not None and adjustment_error_pct > 5.0),
        }
        adjustment_rows.append(row)

        if row["suspect"]:
            start = max(0, before_idx - 10)
            end = min(frame.height, after_idx + 11)
            for px in frame.slice(start, end - start).iter_rows(named=True):
                suspect_detail_rows.append(
                    {
                        "action_symbol": symbol,
                        "action_date": action_date,
                        "expected_ratio": float(action["ratio"]),
                        "actual_ratio": actual_ratio,
                        "adjustment_error_pct": adjustment_error_pct,
                        **px,
                    }
                )
    return adjustment_rows, suspect_detail_rows


def find_signal_overlaps(actions: pl.DataFrame, signals: pl.DataFrame, ohlcv: pl.DataFrame) -> list[dict[str, Any]]:
    overlaps: list[dict[str, Any]] = []
    if actions.is_empty() or signals.is_empty():
        return overlaps

    trading_dates = {
        str(group["symbol"][0]): group.sort("date")["date"].to_list()
        for group in ohlcv.partition_by("symbol", maintain_order=True)
    }
    signals_by_symbol = {
        str(group["symbol"][0]): group.sort("signal_date")
        for group in signals.partition_by("symbol", maintain_order=True)
    }
    for action in actions.iter_rows(named=True):
        symbol = str(action["symbol"])
        action_date = action["date"]
        if isinstance(action_date, datetime):
            action_date = action_date.date()
        dates = trading_dates.get(symbol, [])
        if not dates:
            continue
        _, action_idx = nearest_before_after(dates, action_date)
        if action_idx is None:
            continue
        symbol_signals = signals_by_symbol.get(symbol)
        if symbol_signals is None:
            continue
        date_to_idx = {value: idx for idx, value in enumerate(dates)}
        for sig in symbol_signals.iter_rows(named=True):
            sig_date = sig["signal_date"]
            sig_idx = date_to_idx.get(sig_date)
            if sig_idx is None:
                continue
            trading_days_apart = sig_idx - action_idx
            if abs(trading_days_apart) <= 5:
                overlaps.append(
                    {
                        "symbol": symbol,
                        "signal_date": sig_date,
                        "action_date": action_date,
                        "action_type": action["action_type"],
                        "ratio": float(action["ratio"]),
                        "trading_days_apart": trading_days_apart,
                        "calendar_days_apart": (sig_date - action_date).days,
                        "entry_ref": sig["entry_ref"],
                        "stop": sig["stop"],
                        "pivot": sig["pivot"],
                    }
                )
    return overlaps


def cross_check_seed(seed_path: Path, actions: pl.DataFrame) -> list[dict[str, Any]]:
    if not seed_path.exists():
        return []
    seeds = pl.read_csv(seed_path, try_parse_dates=True).with_columns(
        pl.col("ex_date").cast(pl.Date),
        pl.col("symbol").str.to_uppercase(),
    )
    action_keys = {
        (str(row["symbol"]), row["date"], str(row["action_type"]))
        for row in actions.iter_rows(named=True)
    }
    rows = []
    for seed in seeds.iter_rows(named=True):
        rows.append(
            {
                "symbol": seed["symbol"],
                "ex_date": seed["ex_date"],
                "action_type": seed["action_type"],
                "adjustment_factor": seed["adjustment_factor"],
                "matched_yfinance_same_date_type": (
                    (str(seed["symbol"]), seed["ex_date"], str(seed["action_type"])) in action_keys
                ),
            }
        )
    return rows


def main() -> None:
    args = parse_args()
    args.reference_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cache_path = args.reference_dir / "yfinance_splits_cache.json"
    reference_actions_path = args.reference_dir / "corporate_actions_audit.csv"

    symbols = signal_symbols(args.db)
    if args.limit is not None:
        symbols = symbols[: args.limit]
    cache = load_cache(cache_path)

    fetched = 0
    all_actions: list[dict[str, Any]] = []
    for symbol in symbols:
        if symbol not in cache:
            try:
                cache[symbol] = fetch_splits(symbol)
            except Exception as exc:  # pragma: no cover - network-dependent
                cache[symbol] = {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "status": f"error: {type(exc).__name__}: {exc}",
                    "actions": [],
                }
            fetched += 1
            write_cache(cache_path, cache)
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
        all_actions.extend(cache.get(symbol, {}).get("actions", []))

    actions = pl.DataFrame(
        all_actions,
        schema={
            "symbol": pl.String,
            "date": pl.String,
            "action_type": pl.String,
            "ratio": pl.Float64,
            "source": pl.String,
        },
    )
    if not actions.is_empty():
        actions = actions.with_columns(
            pl.col("symbol").str.to_uppercase(),
            pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
        ).sort(["symbol", "date"])
    actions.write_csv(reference_actions_path)
    actions.write_csv(args.output_dir / "all_actions.csv")

    ohlcv = (
        pl.scan_parquet(args.ohlcv)
        .filter(pl.col("symbol").is_in(symbols))
        .select("date", "symbol", "open", "high", "low", "close", "volume")
        .collect()
        .sort(["symbol", "date"])
    )
    signals = load_signal_frame(args.db)

    adjustment_rows, suspect_detail_rows = validate_actions(actions, ohlcv)
    overlaps = find_signal_overlaps(actions, signals, ohlcv)
    seed_rows = cross_check_seed(args.seed_actions, actions)

    adjustment_errors = [
        row for row in adjustment_rows
        if row.get("adjustment_error_pct") is not None and bool(row.get("suspect"))
    ]
    pl.DataFrame(rows_to_jsonable(suspect_detail_rows)).write_csv(args.output_dir / "suspect_details.csv")
    pl.DataFrame(rows_to_jsonable(adjustment_rows)).write_csv(args.output_dir / "adjustment_checks.csv")
    pl.DataFrame(rows_to_jsonable(overlaps)).write_csv(args.output_dir / "signals_near_corporate_actions.csv")
    pl.DataFrame(rows_to_jsonable(seed_rows)).write_csv(args.output_dir / "seed_crosscheck.csv")

    report = {
        "total_symbols_audited": len(symbols),
        "total_corporate_actions_found": actions.height,
        "splits": actions.filter(pl.col("action_type") == "split").height if not actions.is_empty() else 0,
        "bonuses": actions.filter(pl.col("action_type") == "bonus").height if not actions.is_empty() else 0,
        "yfinance_symbols_fetched": fetched,
        "adjustment_errors": rows_to_jsonable(adjustment_errors),
        "signals_near_corporate_actions": rows_to_jsonable(overlaps),
        "seed_crosscheck": rows_to_jsonable(seed_rows),
        "output_files": {
            "reference_actions": str(reference_actions_path),
            "all_actions": str(args.output_dir / "all_actions.csv"),
            "suspect_details": str(args.output_dir / "suspect_details.csv"),
            "adjustment_checks": str(args.output_dir / "adjustment_checks.csv"),
            "signals_near_corporate_actions": str(args.output_dir / "signals_near_corporate_actions.csv"),
        },
    }
    (args.output_dir / "audit_report.json").write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
