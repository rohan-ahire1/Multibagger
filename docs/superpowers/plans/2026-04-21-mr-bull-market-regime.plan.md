# Plan: Mean Reversion Bull/Bear Regime Study

## Summary

Add a `--regime-study` flag to `strategies/mean_reversion/diagnostics.py` that runs three simulations back-to-back (all periods / bull-only / bear-only) using the default `config.yaml` parameters, prints a side-by-side comparison table, and writes full outputs for each regime to `reports/mean_reversion/regime_study/`.

"Bull" is defined dynamically: entry signals are only accepted on days when the Nifty 50 benchmark (first of `"NIFTY 50"`, `"^NSEI"`, `"NIFTYBEES"` found in the data) is trading above its 200-day SMA. "Bear" is the complement. Open positions are **not** force-closed when the regime flips — only new entries are gated.

---

## Implementation Changes

### 1. `strategies/mean_reversion/simulation.py` — add `build_benchmark_regime()`

Add a standalone helper after `build_mr_symbol_data()` (around line 160):

```python
def build_benchmark_regime(ohlcv: pl.DataFrame) -> dict[date, bool]:
    """Return {date: True} for days benchmark > SMA200, {date: False} otherwise.

    Returns all-True if no benchmark symbol is found in the data.
    """
    benchmark_symbol = None
    for candidate in ("NIFTY 50", "^NSEI", "NIFTYBEES"):
        if candidate in ohlcv["symbol"].unique().to_list():
            benchmark_symbol = candidate
            break
    if benchmark_symbol is None:
        unique_dates = sorted(set(ohlcv["date"].to_list()))
        return {d: True for d in unique_dates}

    bench = (
        ohlcv.filter(pl.col("symbol") == benchmark_symbol)
        .sort("date")
    )
    closes = [float(v) for v in bench["date"].to_list()]   # dates, reused below
    dates_list = bench["date"].to_list()
    close_list = [float(v) for v in bench["close"].to_list()]
    sma200 = sma(close_list, 200)
    result: dict[date, bool] = {}
    for idx, d in enumerate(dates_list):
        if not math.isnan(sma200[idx]):
            result[d] = close_list[idx] > sma200[idx]
    return result
```

(Note: `sma` and `math` are already imported in this file.)

### 2. `strategies/mean_reversion/diagnostics.py`

#### a. `parse_args()` — add flag

```python
parser.add_argument("--regime-study", action="store_true",
                    help="Run bull/bear regime comparison using default config")
```

#### b. `run_variant()` — add `regime` parameter

Add `regime: str = "all"` as a keyword-only parameter (after `round_trip_cost_pct`).

Values: `"all"` | `"bull"` | `"bear"`.

After the existing `signals = scan_mean_reversion(...).filter(...)` call, add:

```python
if regime != "all":
    from strategies.mean_reversion.simulation import build_benchmark_regime
    regime_map = build_benchmark_regime(ohlcv)
    if regime == "bull":
        allowed = {d for d, above in regime_map.items() if above}
    else:
        allowed = {d for d, above in regime_map.items() if not above}
    signals = signals.filter(pl.col("signal_date").is_in(sorted(allowed)))
```

The rest of `run_variant()` is unchanged — simulation receives the filtered signal set.

#### c. Add `run_regime_study()`

New function placed just before `main()`:

```python
def run_regime_study(
    config: dict[str, Any],
    ohlcv: pl.DataFrame,
    universe: set[str],
    sector_map: dict[str, str],
    start_date: date,
    end_date: date,
    output_dir: Path,
    round_trip_cost_pct: float = 0.0,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    common = dict(
        config=config, ohlcv=ohlcv, universe=universe, sector_map=sector_map,
        start_date=start_date, end_date=end_date,
        rsi_threshold=float(config["entry"]["rsi_threshold"]),
        exit_mode=str(config["exit"]["mode"]),
        time_stop_days=int(config["exit"]["time_stop_days"]),
        stop_atr_mult=float(config["entry"]["stop_atr_mult"]),
        risk_per_trade_pct=float(config["portfolio"]["risk_per_trade_pct"]),
        round_trip_cost_pct=round_trip_cost_pct,
    )

    for regime in ("all", "bull", "bear"):
        print(f"\n=== REGIME: {regime.upper()} ===")
        result = run_variant(**common, regime=regime)
        write_outputs(output_dir / regime, result)
        print_summary(result["metrics"])

    # Side-by-side comparison
    rows = []
    for regime in ("all", "bull", "bear"):
        summary_path = output_dir / regime / "summary.json"
        with summary_path.open(encoding="utf-8") as fh:
            m = json.load(fh)
        rows.append({
            "regime": regime,
            "fills": m.get("fills"),
            "cagr_pct": round(m.get("cagr_pct") or 0, 2),
            "max_drawdown_pct": round(m.get("max_drawdown_pct") or 0, 2),
            "calmar": round(m.get("calmar") or 0, 3),
            "sharpe": round(m.get("sharpe") or 0, 3),
            "win_rate": round((m.get("win_rate") or 0) * 100, 1),
            "avg_holding_days": round(m.get("avg_holding_days") or 0, 1),
            "profit_factor": round(m.get("profit_factor") or 0, 3),
        })
    frame = pl.DataFrame(rows)
    frame.write_csv(output_dir / "regime_comparison.csv")
    print("\n=== REGIME COMPARISON ===")
    print(frame)
    print(f"\nWrote outputs to {output_dir}")
```

#### d. `main()` — wire the new flag

In the `if/elif` block after `if args.short_side:`, add before the default `run_variant` call:

```python
if args.regime_study:
    run_regime_study(config, ohlcv, universe, sector_map, start_date, end_date,
                     output_root / "regime_study", round_trip_cost_pct=args.cost)
    return
```

---

## Test Plan

- [ ] `python strategies/mean_reversion/diagnostics.py --regime-study` runs without error.
- [ ] `reports/mean_reversion/regime_study/{all,bull,bear}/summary.json` all exist and contain non-zero `fills`.
- [ ] `reports/mean_reversion/regime_study/regime_comparison.csv` has exactly 3 rows.
- [ ] Bull fills + bear fills == all fills (every signal belongs to exactly one regime).
- [ ] `--regime-study --start 2020-01-01 --end 2024-12-31` respects date bounds.
- [ ] Existing flags (`--sweep`, `--sweep-v2`, `--short-side`) still run unchanged.

---

## Assumptions

- The existing `sma()` helper in `indicators.py` handles fewer than 200 data points by returning `math.nan` for those indices; `build_benchmark_regime()` skips those dates, which means the first ~200 trading days of the benchmark have no regime classification and those entry dates are excluded in bull/bear runs. This is acceptable and consistent with how `build_mr_symbol_data()` already handles it.
- Open positions entered in one regime are not force-closed if the regime changes mid-hold. Only new entries are gated.
- If no benchmark symbol is present in the loaded OHLCV (unusual), `build_benchmark_regime()` returns all-True so "bull" = "all" and "bear" = empty.
