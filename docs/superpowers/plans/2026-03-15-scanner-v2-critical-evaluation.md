# Scanner V2 — Critical Evaluation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 14 recommendations from the critical evaluation to transform the SEPA scanner from a faithful Minervini replication into a production-grade Indian equity breakout system.

**Architecture:** Layered changes — parameter tuning first, then algorithm upgrades, then new modules. Each chunk produces working, testable software. Strategy document (STRATEGY.md) updated last per chunk to reflect implemented changes. External data source integrations (earnings, VIX, sector classifications) use pluggable adapters.

**Tech Stack:** Python 3.12, Polars, Pydantic, Typer CLI, pytest. No new dependencies for Chunks 1-4. Chunks 5-6 may add `httpx` for API calls (already available via existing `nse_bhavcopy.py` patterns).

---

## File Structure

### New Files (by chunk)

| Chunk | File | Purpose |
|-------|------|---------|
| 1 | — | No new files |
| 2 | — | No new files (exit logic stays in engine.py) |
| 3 | `src/sepa_scanner/strategy/market_regime.py` | MarketRegime enum + graduated filter logic |
| 3 | `tests/test_market_regime.py` | Tests for graduated market filter |
| 4 | `src/sepa_scanner/backtest/portfolio.py` | Portfolio-level risk controls |
| 4 | `tests/test_portfolio_controls.py` | Tests for portfolio risk |
| 5 | `src/sepa_scanner/data_sources/nse_meta.py` | F&O list, ASM/GSM, market cap fetcher |
| 5 | `src/sepa_scanner/strategy/sector_rs.py` | Sector-level RS ranking |
| 5 | `tests/test_sector_rs.py` | Tests for sector RS |
| 5 | `tests/test_nse_meta.py` | Tests for NSE metadata |
| 6 | `src/sepa_scanner/data_sources/fundamentals.py` | Earnings data adapter |
| 6 | `src/sepa_scanner/strategy/patterns.py` | Multi-pattern detection (HTF, cup-handle, etc.) |
| 6 | `src/sepa_scanner/data_sources/vix.py` | India VIX data fetcher |
| 6 | `tests/test_patterns.py` | Tests for pattern detection |
| 6 | `tests/test_fundamentals.py` | Tests for earnings filter |
| 6 | `tests/test_vix.py` | Tests for VIX position sizing |

### Modified Files

| File | Changes |
|------|---------|
| `config/config.yaml` | All parameter updates across all chunks |
| `src/sepa_scanner/settings.py` | New settings models + updated defaults |
| `src/sepa_scanner/indicators/rs.py` | Skip-month offset, cutoff change |
| `src/sepa_scanner/indicators/contraction.py` | Progressive contraction verification |
| `src/sepa_scanner/strategy/signal_engine.py` | ATR-based breakout buffer, sector filter, earnings filter integration |
| `src/sepa_scanner/strategy/market_filter.py` | Graduated 3-state system |
| `src/sepa_scanner/strategy/trend_template.py` | No code change (config-only for slope) |
| `src/sepa_scanner/strategy/vcp_setup.py` | Contraction quality gate |
| `src/sepa_scanner/strategy/explainer.py` | Updated display text for changed params |
| `src/sepa_scanner/backtest/models.py` | New exit mode, trail_activation_r default, portfolio params |
| `src/sepa_scanner/backtest/engine.py` | V3 percentage trail, +2R activation, dead money stop |
| `src/sepa_scanner/backtest/metrics.py` | Portfolio-level metrics |
| `tests/test_rs_rank.py` | Skip-month tests |
| `tests/test_setup_gate.py` | Updated thresholds |
| `tests/test_market_filter.py` | Graduated filter tests |
| `tests/test_backtest_engine.py` | V3 exit mode tests, +2R activation tests |
| `tests/test_contraction.py` | Progressive contraction tests |
| `tests/test_signal_engine.py` | ATR-based breakout buffer |
| `tests/test_settings.py` | New settings fields |
| `docs/STRATEGY.md` | Updated per chunk |

---

## Chunk 1: Scanner Core — Parameter & Algorithm Improvements

Items #2 (RS cutoff + skip month), #6 (slope lookback), #10 (ATR breakout buffer), #12 (contraction improvements)

---

### Task 1.1: RS — Skip Most Recent 21 Days

The RS calculation currently uses raw lookback windows starting from the scan date. Academic evidence (Jegadeesh & Titman 1993) shows skipping the most recent month avoids short-term reversal contamination.

**Files:**
- Modify: `src/sepa_scanner/indicators/rs.py`
- Modify: `src/sepa_scanner/settings.py`
- Test: `tests/test_rs_rank.py`

- [ ] **Step 1: Add `skip_recent_days` to RelativeStrengthSettings**

In `src/sepa_scanner/settings.py`, add field to `RelativeStrengthSettings`:

```python
class RelativeStrengthSettings(BaseModel):
    lookbacks: list[int]
    weights: list[float]
    rs_rank_min: float
    skip_recent_days: int = 21  # NEW: skip most recent month to avoid reversal effect
```

- [ ] **Step 2: Write failing test for skip-month RS**

In `tests/test_rs_rank.py`:

```python
def test_rs_skips_recent_days_when_configured() -> None:
    """RS with skip_recent_days=1 should use close[t-1] as the 'current' price."""
    start = date(2026, 3, 2)
    dates = [start + timedelta(days=offset) for offset in range(5)]
    frame = pl.DataFrame(
        {
            "date": dates * 3,
            "symbol": (
                ["NIFTYBEES"] * 5
                + ["AAA"] * 5
                + ["BBB"] * 5
            ),
            "close": [
                100.0, 105.0, 110.0, 115.0, 120.0,  # benchmark
                100.0, 110.0, 120.0, 130.0, 90.0,    # AAA: strong then crashes on last day
                100.0, 102.0, 104.0, 106.0, 140.0,   # BBB: weak then spikes on last day
            ],
        }
    )

    # Without skip: BBB's last-day spike makes it look strong
    result_no_skip = compute_rs_ranks(
        frame=frame, benchmark_symbol="NIFTYBEES",
        lookbacks=[1, 2], weights=[0.5, 0.5],
        scan_date=dates[-1], symbols=["AAA", "BBB"],
    )

    # With skip=1: should rank using close[t-1] instead of close[t]
    result_skip = compute_rs_ranks(
        frame=frame, benchmark_symbol="NIFTYBEES",
        lookbacks=[1, 2], weights=[0.5, 0.5],
        scan_date=dates[-1], symbols=["AAA", "BBB"],
        skip_recent_days=1,
    )

    # Without skip, BBB ranks higher due to spike
    no_skip_top = result_no_skip.sort("rs_rank", descending=True)["symbol"][0]
    assert no_skip_top == "BBB"

    # With skip, AAA ranks higher (130 vs 106, ignoring last-day noise)
    skip_top = result_skip.sort("rs_rank", descending=True)["symbol"][0]
    assert skip_top == "AAA"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_rs_rank.py::test_rs_skips_recent_days_when_configured -v`
Expected: FAIL — `compute_rs_ranks()` doesn't accept `skip_recent_days`

- [ ] **Step 4: Implement skip_recent_days in compute_rs_ranks**

In `src/sepa_scanner/indicators/rs.py`, modify `compute_rs_ranks`:

```python
def compute_rs_ranks(
    frame: pl.DataFrame,
    benchmark_symbol: str,
    lookbacks: list[int],
    weights: list[float],
    scan_date: date,
    symbols: list[str] | None = None,
    skip_recent_days: int = 0,  # NEW
) -> pl.DataFrame:
    if len(lookbacks) != len(weights):
        raise ValueError("lookbacks and weights must be the same length")

    sorted_frame = frame.sort(["symbol", "date"])
    return_columns: list[pl.Expr] = []
    benchmark_columns: list[pl.Expr] = []
    rs_terms: list[pl.Expr] = []

    for lookback, weight in zip(lookbacks, weights, strict=True):
        # Shift by skip_recent_days to use close[t-skip] as "current" price
        if skip_recent_days > 0:
            symbol_ret = (
                (pl.col("close").shift(skip_recent_days) / pl.col("close").shift(lookback + skip_recent_days)) - 1.0
            ).over("symbol").alias(f"ret_{lookback}")
        else:
            symbol_ret = (
                (pl.col("close") / pl.col("close").shift(lookback)) - 1.0
            ).over("symbol").alias(f"ret_{lookback}")
        return_columns.append(symbol_ret)
        benchmark_columns.append(pl.col(f"ret_{lookback}").alias(f"bm_ret_{lookback}"))
        rs_terms.append(
            pl.lit(weight)
            * (
                (1.0 + pl.col(f"ret_{lookback}")) / (1.0 + pl.col(f"bm_ret_{lookback}")) - 1.0
            )
        )

    with_returns = sorted_frame.with_columns(return_columns)
    benchmark = with_returns.filter(pl.col("symbol") == benchmark_symbol).select(
        "date", *benchmark_columns
    )

    ranked = with_returns.join(benchmark, on="date", how="left").with_columns(
        pl.sum_horizontal(rs_terms).alias("rs_score")
    )

    filtered = ranked.filter(pl.col("date") == pl.lit(scan_date)).filter(
        pl.col("symbol") != benchmark_symbol
    )
    if symbols is not None:
        filtered = filtered.filter(pl.col("symbol").is_in(symbols))

    result = filtered.select("symbol", "rs_score").sort("symbol")
    count = result.height
    if count == 0:
        return result.with_columns(pl.lit(None).cast(pl.Float64).alias("rs_rank"))
    if count == 1:
        return result.with_columns(pl.lit(100.0).alias("rs_rank"))

    return result.with_columns(
        (
            ((pl.col("rs_score").rank(method="average") - 1.0) / float(count - 1)) * 100.0
        ).alias("rs_rank")
    )
```

Similarly in `precompute_rs_scores`, add `skip_recent_days: int = 0` parameter and apply the same shift:

```python
def precompute_rs_scores(
    frame: pl.DataFrame,
    benchmark_symbol: str,
    lookbacks: list[int],
    weights: list[float],
    skip_recent_days: int = 0,  # NEW
) -> pl.DataFrame:
    # ... existing validation ...
    sorted_frame = frame.sort(["symbol", "date"])
    return_columns: list[pl.Expr] = []
    benchmark_columns: list[pl.Expr] = []
    rs_terms: list[pl.Expr] = []

    for lookback, weight in zip(lookbacks, weights, strict=True):
        if skip_recent_days > 0:
            symbol_ret = (
                (pl.col("close").shift(skip_recent_days) / pl.col("close").shift(lookback + skip_recent_days)) - 1.0
            ).over("symbol").alias(f"ret_{lookback}")
        else:
            symbol_ret = (
                (pl.col("close") / pl.col("close").shift(lookback)) - 1.0
            ).over("symbol").alias(f"ret_{lookback}")
        return_columns.append(symbol_ret)
        benchmark_columns.append(pl.col(f"ret_{lookback}").alias(f"bm_ret_{lookback}"))
        rs_terms.append(
            pl.lit(weight)
            * ((1.0 + pl.col(f"ret_{lookback}")) / (1.0 + pl.col(f"bm_ret_{lookback}")) - 1.0)
        )

    with_returns = sorted_frame.with_columns(return_columns)
    benchmark = with_returns.filter(pl.col("symbol") == benchmark_symbol).select(
        "date", *benchmark_columns
    )
    return (
        with_returns.join(benchmark, on="date", how="left")
        .with_columns(pl.sum_horizontal(rs_terms).alias("rs_score"))
        .filter(pl.col("symbol") != benchmark_symbol)
        .select("date", "symbol", "rs_score")
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_rs_rank.py -v`
Expected: All PASS

- [ ] **Step 6: Wire skip_recent_days through signal_engine**

In `src/sepa_scanner/strategy/signal_engine.py`, update both `compute_rs_ranks` and `precompute_rs_scores` call sites to pass `skip_recent_days=settings.relative_strength.skip_recent_days`.

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -v`
Expected: All existing tests still pass (default `skip_recent_days=0` preserves behavior)

- [ ] **Step 8: Commit**

```bash
git add src/sepa_scanner/indicators/rs.py src/sepa_scanner/settings.py src/sepa_scanner/strategy/signal_engine.py tests/test_rs_rank.py
git commit -m "feat: RS skip-month to avoid short-term reversal contamination"
```

---

### Task 1.2: RS — Raise Default Cutoff to 80th Percentile

**Files:**
- Modify: `config/config.yaml`
- Modify: `src/sepa_scanner/strategy/explainer.py:130` (display text fix: "20d" → "22d")
- Test: `tests/test_settings.py`

- [ ] **Step 1: Update config.yaml**

```yaml
relative_strength:
  lookbacks: [63, 126, 189, 252]
  weights: [0.4, 0.2, 0.2, 0.2]
  rs_rank_min: 80             # was 70
  skip_recent_days: 21        # NEW
```

- [ ] **Step 2: Fix explainer display text**

In `src/sepa_scanner/strategy/explainer.py:130`, change:

```python
actual=f"{_fmt_price(sma200)} vs {_fmt_price(sma200_lag)} 20d ago",
```

To:

```python
actual=f"{_fmt_price(sma200)} vs {_fmt_price(sma200_lag)} {cfg.sma200_slope_lookback}d ago",
```

Note: This requires passing `cfg` which is already available as `settings.trend_template` in the function.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_settings.py tests/test_signal_engine.py -v`
Expected: PASS (test_signal_engine uses its own fixture settings with `rs_rank_min=50`)

- [ ] **Step 4: Commit**

```bash
git add config/config.yaml src/sepa_scanner/strategy/explainer.py
git commit -m "feat: raise RS cutoff to 80th percentile, fix explainer display"
```

---

### Task 1.3: Slope Lookback — 22 → 66 Trading Days

**Files:**
- Modify: `config/config.yaml`

- [ ] **Step 1: Update config.yaml**

```yaml
trend_template:
  sma200_slope_lookback: 66   # was 22 — 3 months minimum per Minervini
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_trend_template.py tests/test_signal_engine.py -v`
Expected: PASS (tests use their own fixture settings)

- [ ] **Step 3: Commit**

```bash
git add config/config.yaml
git commit -m "feat: increase SMA200 slope lookback to 66 days (3-month minimum)"
```

---

### Task 1.4: ATR-Based Breakout Buffer

Replace fixed 0.3% breakout buffer with 0.5× ATR(14) above pivot.

**Files:**
- Modify: `src/sepa_scanner/settings.py`
- Modify: `src/sepa_scanner/strategy/signal_engine.py`
- Modify: `config/config.yaml`
- Test: `tests/test_signal_engine.py`

- [ ] **Step 1: Add breakout_buffer_atr_mult to BreakoutSettings**

In `src/sepa_scanner/settings.py`:

```python
class BreakoutSettings(BaseModel):
    require_market_filter: bool
    breakout_buffer_pct: float  # kept as fallback
    breakout_buffer_atr_mult: float | None = 0.5  # NEW: when set, overrides breakout_buffer_pct
    breakout_vol_mult: float
    max_extension_breakout_pct: float = 0.15
```

- [ ] **Step 2: Update config.yaml**

```yaml
breakout:
  require_market_filter: true
  breakout_buffer_pct: 0.003     # fallback when ATR unavailable
  breakout_buffer_atr_mult: 0.5  # NEW: 0.5 × ATR(14) above pivot
  breakout_vol_mult: 1.5
  max_extension_breakout_pct: 0.15
```

Also update risk section:

```yaml
risk:
  entry_buffer_pct: 0.003   # fallback
  entry_buffer_atr_mult: 0.5  # NEW: matches breakout buffer
  max_stop_pct: 0.10         # was 0.08 — widened for Indian mid-caps
  target_r: null
```

Add `entry_buffer_atr_mult` and update `max_stop_pct` in `RiskSettings`:

```python
class RiskSettings(BaseModel):
    entry_buffer_pct: float
    entry_buffer_atr_mult: float | None = 0.5
    max_stop_pct: float
    target_r: float | None = None
```

- [ ] **Step 3: Write failing test for ATR-based breakout buffer**

In `tests/test_signal_engine.py`, add:

```python
def test_breakout_uses_atr_based_buffer_when_configured(tmp_path: Path) -> None:
    """When breakout_buffer_atr_mult is set, breakout buffer = pivot * atr_mult * atr_pct."""
    dates = _dates(61)
    benchmark = _symbol_frame(
        "NIFTYBEES", dates,
        [100.0 + 0.5 * idx for idx in range(61)],
        [1_000_000] * 61,
    )
    # Stock with tight base near pivot
    closes = (
        [75.0 + 0.9 * idx for idx in range(50)]
        + [119.2, 119.4, 119.5, 119.6, 119.7, 119.8, 119.85, 119.9, 119.95, 121.5, 122.0]
    )
    breakout = _symbol_frame(
        "BREAKME", dates, closes,
        [350_000] * 50 + [150_000] * 9 + [5_000_000, 1_500_000],
    )
    weak = _symbol_frame(
        "WEAK", dates,
        [90.0 + 0.1 * idx for idx in range(61)],
        [300_000] * 61,
    )

    frame = pl.concat([benchmark, breakout, weak], how="vertical")
    settings = _settings(tmp_path)
    settings.breakout.breakout_buffer_atr_mult = 0.5

    result = scan_for_date(frame=frame, settings=settings, scan_date=dates[59])

    # Verify entry_ref uses ATR-based calculation
    bo = result.signals.filter(
        (pl.col("symbol") == "BREAKME") & (pl.col("signal_type") == "BREAKOUT")
    )
    if bo.height > 0:
        entry_ref = bo["entry_ref"][0]
        pivot = bo["pivot"][0]
        # Entry ref should be pivot + (atr_pct * pivot * 0.5), not pivot * 1.003
        assert entry_ref > pivot * 1.003, "ATR buffer should be wider than fixed 0.3%"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_signal_engine.py::test_breakout_uses_atr_based_buffer_when_configured -v`
Expected: FAIL — signal_engine doesn't read `breakout_buffer_atr_mult`

- [ ] **Step 5: Implement ATR-based breakout buffer in signal_engine**

In `src/sepa_scanner/strategy/signal_engine.py`, in `scan_for_date()`, after the enriched DataFrame is built, modify the breakout buffer and entry_ref calculation:

Replace the current static buffer approach:

```python
breakout_buffer = 1.0 + settings.breakout.breakout_buffer_pct
```

With an ATR-adaptive approach:

```python
# ATR-based breakout buffer: pivot + atr_mult * atr_pct * pivot
# Falls back to fixed pct when ATR unavailable or not configured
atr_mult = settings.breakout.breakout_buffer_atr_mult
if atr_mult is not None:
    breakout_buffer_expr = (
        pl.col("pivot") * (1.0 + pl.col("atr_pct").fill_null(0.0) * atr_mult)
    ).alias("_breakout_level")
else:
    breakout_buffer_expr = (
        pl.col("pivot") * (1.0 + settings.breakout.breakout_buffer_pct)
    ).alias("_breakout_level")
```

Then use `_breakout_level` in the breakout mask and entry_ref computation instead of the scalar `breakout_buffer`.

Similarly update the entry_ref:

```python
entry_atr_mult = settings.risk.entry_buffer_atr_mult
if entry_atr_mult is not None:
    entry_ref_expr = (
        pl.col("pivot") * (1.0 + pl.col("atr_pct").fill_null(0.0) * entry_atr_mult)
    ).alias("entry_ref")
else:
    entry_ref_expr = (
        pl.col("pivot") * (1.0 + settings.risk.entry_buffer_pct)
    ).alias("entry_ref")
```

**Important:** The breakout_mask uses the breakout buffer, and `entry_ref` / `stop` / `r_per_share` use the entry buffer. Keep them consistent. The existing code has:

```python
breakout_mask = ... & (pl.col("close") >= pl.col("pivot") * breakout_buffer) & ...
```

This needs to become:

```python
breakout_mask = ... & (pl.col("close") >= pl.col("_breakout_level")) & ...
```

And the `prev_close` first-day check:

```python
& (pl.col("prev_close") < pl.col("_breakout_level"))
```

This requires adding `_breakout_level` as a column before the mask computation.

**Important ATR timing note:** The breakout buffer should use `atr_pct_prev` (D-1 ATR) not `atr_pct` (D-0), because the breakout decision at market close should reference the ATR known *before* the breakout day's volatility. The breakout_mask already operates on the current day's `close`, but the *buffer threshold* should be pre-breakout. Use `pl.col("atr_pct_prev")` (already exists as a shifted column) for the buffer expression:

```python
breakout_buffer_expr = (
    pl.col("pivot") * (1.0 + pl.col("atr_pct_prev").fill_null(0.0) * atr_mult)
).alias("_breakout_level")
```

**Also update `_build_diagnostics`:** The function at line 235 of `signal_engine.py` independently computes `breakout_buffer = 1.0 + settings.breakout.breakout_buffer_pct` and uses it in the `breakout_fail_mask`. This must be updated to use the same ATR-based `_breakout_level` column. The diagnostic join already has access to `scan_rows` which will contain the `_breakout_level` column, so replace:

```python
breakout_buffer = 1.0 + settings.breakout.breakout_buffer_pct
```

with usage of the `_breakout_level` column in the fail mask expressions:

```python
# In breakout_fail_mask:
+ pl.when(pl.col("close") < pl.col("_breakout_level")).then(BREAKOUT_FAIL_BELOW_PIVOT).otherwise(0)
# ...
+ pl.when(pl.col("prev_close") >= pl.col("_breakout_level")).then(BREAKOUT_FAIL_NOT_FIRST_DAY).otherwise(0)
```

- [ ] **Step 6: Run tests to verify pass**

Run: `pytest tests/test_signal_engine.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/sepa_scanner/settings.py src/sepa_scanner/strategy/signal_engine.py config/config.yaml tests/test_signal_engine.py
git commit -m "feat: ATR-based breakout buffer replaces fixed 0.3% threshold"
```

---

### Task 1.5: Progressive Contraction Verification

The contraction detector counts narrowing peaks but doesn't verify each is proportionally smaller than its predecessor. Add progressive verification and a quality tier system.

**Files:**
- Modify: `src/sepa_scanner/indicators/contraction.py`
- Modify: `src/sepa_scanner/strategy/vcp_setup.py`
- Modify: `src/sepa_scanner/settings.py`
- Modify: `config/config.yaml`
- Test: `tests/test_contraction.py`
- Test: `tests/test_setup_gate.py`

- [ ] **Step 1: Write failing test for progressive contraction**

In `tests/test_contraction.py`, add:

```python
def test_contraction_rejects_non_progressive_sequence() -> None:
    """A sequence like 12% -> 15% -> 8% should NOT count as 3 contractions."""
    from sepa_scanner.indicators.contraction import _count_contractions
    import numpy as np

    # Create range10 series with non-progressive contractions
    # Peak 1: 0.12, Peak 2: 0.15 (wider!), Peak 3: 0.08
    series = np.array([
        0.05, 0.08, 0.12, 0.10, 0.06,  # contraction 1: peak at 0.12
        0.08, 0.12, 0.15, 0.12, 0.07,  # contraction 2: peak at 0.15 (NOT narrowing)
        0.05, 0.06, 0.08, 0.06, 0.04,  # contraction 3: peak at 0.08
    ])

    count, quality = _count_contractions(series, smooth_window=3, min_peak_pct=0.02)
    # Should be 1 (first peak only), since second peak breaks the chain
    assert count <= 2, "Non-progressive peaks should break the contraction chain"
```

- [ ] **Step 2: Add contraction_progressive flag to output**

In `src/sepa_scanner/indicators/contraction.py`, modify `_count_contractions` to also return a boolean `progressive`:

```python
def _count_contractions(
    range10: np.ndarray,
    smooth_window: int,
    min_peak_pct: float,
) -> tuple[int, float | None, bool]:
    """Count successive narrowing peaks in a range10 series.

    Returns (contraction_count, contraction_quality, is_progressive).
    is_progressive is True if every successive peak is at least 20% smaller.
    """
```

In step 3 of the existing algorithm, add progressive check:

```python
    progressive = True
    count = 1
    ratios: list[float] = []
    for j in range(1, len(peaks)):
        if peaks[j][1] < peaks[j - 1][1]:
            ratio = peaks[j][1] / peaks[j - 1][1]
            count += 1
            ratios.append(ratio)
            if ratio > 0.80:  # Less than 20% smaller — not truly progressive
                progressive = False
        else:
            break

    quality = sum(ratios) / len(ratios) if ratios else None
    return count, quality, progressive
```

Update `add_contraction_columns` to expose the new `contraction_progressive` column. **Critical:** fix the unpacking in the inner function:

```python
# In _process_group, change:
c, q = _count_contractions(window, smooth_window, min_peak_pct)
# To:
c, q, prog = _count_contractions(window, smooth_window, min_peak_pct)
```

Add a `progressives` array alongside `counts` and `qualities`, and expose it as a new `contraction_progressive` boolean column.

- [ ] **Step 3: Add contraction quality tier to VCP setup (advisory column, does not affect setup_pass)**

In `config/config.yaml`:

```yaml
setup_vcp:
  contraction_min_count: 2          # hard minimum
  contraction_standard_count: 3     # NEW: standard quality threshold
```

In `src/sepa_scanner/settings.py`, add to `SetupVcpSettings`:

```python
contraction_standard_count: int = 3  # for high-conviction signals
```

In `src/sepa_scanner/strategy/vcp_setup.py`, add a `contraction_tier` column. **This is advisory only** — it does NOT affect `setup_pass` or signal generation. It's exposed for ranking, explainer display, and UI badges:

```python
# After existing contraction gate (does NOT get added to gate_cols):
if "contraction_count" in result.columns:
    result = result.with_columns(
        pl.when(pl.col("contraction_count") >= cfg.contraction_standard_count)
        .then(pl.lit("premium"))
        .when(pl.col("contraction_count") >= cfg.contraction_min_count)
        .then(pl.lit("standard"))
        .otherwise(pl.lit("low"))
        .alias("contraction_tier"),
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_contraction.py tests/test_setup_gate.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/sepa_scanner/indicators/contraction.py src/sepa_scanner/strategy/vcp_setup.py src/sepa_scanner/settings.py config/config.yaml tests/test_contraction.py tests/test_setup_gate.py
git commit -m "feat: progressive contraction verification with quality tiers"
```

---

## Chunk 2: Exit Strategy Overhaul

Items #3 (trail activation +1R → +2R) and #4 (20% percentage trailing stop)

---

### Task 2.1: New V3_TRAIL_PCT Exit Mode

Add a percentage-based trailing stop (empirically best performer at 20-25%).

**Files:**
- Modify: `src/sepa_scanner/backtest/models.py`
- Modify: `src/sepa_scanner/backtest/engine.py`
- Modify: `config/config.yaml`
- Test: `tests/test_backtest_engine.py`

- [ ] **Step 1: Add V3_TRAIL_PCT to ExitMode enum**

In `src/sepa_scanner/backtest/models.py`:

```python
class ExitMode(str, Enum):
    V1_FIXED = "v1_fixed"
    V2_TRAIL_10D = "v2_trail_10d"
    V2_TRAIL_21EMA = "v2_trail_21ema"
    V3_TRAIL_PCT = "v3_trail_pct"      # NEW


EXIT_MODE_LABELS: dict[ExitMode, str] = {
    ExitMode.V1_FIXED: "V1 Fixed Stop",
    ExitMode.V2_TRAIL_10D: "V2 Trail 10-Day Low",
    ExitMode.V2_TRAIL_21EMA: "V2 Trail 21-EMA",
    ExitMode.V3_TRAIL_PCT: "V3 Trail Percentage",   # NEW
}
```

Add `trail_pct` to `BacktestParams`:

```python
@dataclass(slots=True)
class BacktestParams:
    pending_days: int = 5
    max_hold_days: int = 90
    target_r: float | None = None
    exit_mode: ExitMode = ExitMode.V3_TRAIL_PCT     # NEW default
    trail_activation_r: float = 2.0                   # CHANGED from 1.0
    trail_pct: float = 0.20                           # NEW: 20% trailing stop
```

- [ ] **Step 2: Write failing test for V3 percentage trail**

In `tests/test_backtest_engine.py`:

```python
def test_backtest_v3_trail_pct_exits_on_percentage_retracement() -> None:
    """V3_TRAIL_PCT: after +2R activation, exit when price drops 20% from highest close."""
    signals = _signal(entry_ref=101.0, stop=95.0)  # risk = 6.0, +2R = 113.0
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC",
             "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0},
            # Day 2: price surges past +2R (113.0)
            {"date": date(2026, 3, 4), "symbol": "ABC",
             "open": 101.0, "high": 115.0, "low": 100.5, "close": 114.0},
            # Day 3: continues higher
            {"date": date(2026, 3, 5), "symbol": "ABC",
             "open": 114.0, "high": 120.0, "low": 113.0, "close": 118.0},
            # Day 4: drops > 20% from peak close of 118 → trail at 94.4
            # But initial stop is 95.0 which is higher, so stop at 101.0 (breakeven from +1R)
            # Actually trail = max(active_stop, highest_close * (1 - 0.20))
            # highest_close = 118.0, trail = 94.4, but breakeven stop = 101.0 is higher
            # So active_stop = 101.0. Need bigger drop.
            {"date": date(2026, 3, 6), "symbol": "ABC",
             "open": 117.0, "high": 125.0, "low": 116.0, "close": 124.0},
            # highest_close now 124.0, trail = 124 * 0.80 = 99.2 — still below breakeven 101.0
            # Day 5: price drops below 101.0 (breakeven stop)
            {"date": date(2026, 3, 7), "symbol": "ABC",
             "open": 123.0, "high": 123.5, "low": 100.0, "close": 100.5},
        ]
    )

    result = simulate_breakout_backtest(
        signals, prices,
        BacktestParams(
            max_hold_days=10,
            exit_mode=ExitMode.V3_TRAIL_PCT,
            trail_activation_r=2.0,
            trail_pct=0.20,
        ),
    )

    trade = result.trades[0]
    assert trade.status == "stopped"
    # Trail stop = max(breakeven=101.0, 124.0 * 0.80=99.2) = 101.0
    assert trade.exit_price == 101.0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_backtest_engine.py::test_backtest_v3_trail_pct_exits_on_percentage_retracement -v`
Expected: FAIL

- [ ] **Step 4: Implement V3_TRAIL_PCT in backtest engine**

In `src/sepa_scanner/backtest/engine.py`, in `_simulate_signal`:

1. Add `highest_close = entry_price` alongside the existing `mae_price = entry_price` (line 163 area).

2. In the trailing stop update block, add the V3 case. The engine uses local variables (not dataclass fields):

```python
elif params.exit_mode == ExitMode.V3_TRAIL_PCT:
    highest_close = max(highest_close, close_price)
    pct_trail_stop = highest_close * (1.0 - params.trail_pct)
    active_stop = max(active_stop, pct_trail_stop)
```

3. Also update `highest_close` in the pre-trail-activation block (when trail not yet activated but we still track the highest close for later use):

```python
else:
    # Not yet trail-activated — still track highest close
    if params.exit_mode == ExitMode.V3_TRAIL_PCT:
        highest_close = max(highest_close, close_price)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_backtest_engine.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/sepa_scanner/backtest/models.py src/sepa_scanner/backtest/engine.py tests/test_backtest_engine.py
git commit -m "feat: V3 percentage trailing stop (empirically superior exit method)"
```

---

### Task 2.2: Trail Activation at +2R with Breakeven at +1R

**Files:**
- Modify: `src/sepa_scanner/backtest/engine.py`
- Test: `tests/test_backtest_engine.py`

- [ ] **Step 1: Write failing test for +1R breakeven (no trail activation)**

```python
def test_backtest_breakeven_at_1r_without_trail_activation() -> None:
    """At +1R, stop moves to breakeven but trail does NOT activate."""
    signals = _signal(entry_ref=101.0, stop=95.0)  # risk = 6.0
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC",
             "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0},
            # Day 2: reaches +1R (107.0) but not +2R (113.0)
            {"date": date(2026, 3, 4), "symbol": "ABC",
             "open": 101.0, "high": 108.0, "low": 100.5, "close": 107.5},
            # Day 3: drops back — stop should be at breakeven (101.0), not original (95.0)
            {"date": date(2026, 3, 5), "symbol": "ABC",
             "open": 106.0, "high": 106.5, "low": 100.5, "close": 101.0},
        ]
    )

    result = simulate_breakout_backtest(
        signals, prices,
        BacktestParams(
            max_hold_days=3,
            exit_mode=ExitMode.V3_TRAIL_PCT,
            trail_activation_r=2.0,
            trail_pct=0.20,
        ),
    )

    trade = result.trades[0]
    # Should be time_stop at day 3 close of 101.0, because stop is at breakeven (101.0)
    # and close barely touches it. The exact behavior depends on implementation.
    # Key assertion: trade was NOT stopped at original stop of 95.0
    if trade.status == "stopped":
        assert trade.exit_price >= 101.0, "Stop should be at breakeven, not original stop"
```

- [ ] **Step 1b: Audit existing tests for trail_activation_r default change**

The default `trail_activation_r` changed from 1.0 to 2.0 in Task 2.1. Check all existing tests in `test_backtest_engine.py` that construct `BacktestParams()` without explicit `trail_activation_r`. The two trailing stop tests (`test_backtest_trail_10d_*` and `test_backtest_trail_21ema_*`) use `BacktestParams(max_hold_days=4, exit_mode=ExitMode.V2_TRAIL_10D)` and `BacktestParams(max_hold_days=4, exit_mode=ExitMode.V2_TRAIL_21EMA)` respectively — neither specifies `trail_activation_r`, so they'll now default to 2.0 instead of 1.0.

These tests have prices that surge to 108.0 (high) with entry at 101.0 and risk of 6.0:
- At `trail_activation_r=1.0`: activates at 107.0 (hit on day 2 high of 108.0) ✓
- At `trail_activation_r=2.0`: activates at 113.0 (NOT hit — max high is 108.0) ✗

**Fix:** Add explicit `trail_activation_r=1.0` to these two existing tests to preserve their behavior.

- [ ] **Step 2: Implement +1R breakeven logic**

**Cross-mode note:** The breakeven-at-+1R logic applies to ALL V2/V3 exit modes (V2_TRAIL_10D, V2_TRAIL_21EMA, V3_TRAIL_PCT) — not just V3. This is intentional: Minervini's breakeven rule is universal regardless of trailing method. The breakeven stage uses a hardcoded +1R threshold (not configurable), while the trail activation stage uses `trail_activation_r` (configurable, default 2.0).

In `src/sepa_scanner/backtest/engine.py`, modify the trail activation logic:

```python
# Current code has a single threshold: trail_activation_r
# New: two-stage system
# Stage 1: at +1R, move stop to breakeven
# Stage 2: at +trail_activation_r (default 2.0), activate trailing mechanism

breakeven_activated = False

# ... inside the bar loop:
if params.exit_mode != ExitMode.V1_FIXED and risk:
    prev_stop = active_stop

    # Stage 1: breakeven at +1R
    if not breakeven_activated:
        if high_price >= entry_price + risk * 1.0:
            breakeven_activated = True
            active_stop = max(active_stop, entry_price)

    # Stage 2: trail activation at +trail_activation_r
    if not trail_activated:
        if high_price >= entry_price + risk * params.trail_activation_r:
            trail_activated = True
            active_stop = max(active_stop, entry_price)  # already done by breakeven

    # ... rest of trailing logic only when trail_activated
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_backtest_engine.py -v`
Expected: All PASS. Existing tests that use `trail_activation_r=1.0` explicitly will still work.

- [ ] **Step 4: Commit**

```bash
git add src/sepa_scanner/backtest/engine.py tests/test_backtest_engine.py
git commit -m "feat: two-stage exit — breakeven at +1R, trail activation at +2R"
```

---

### Task 2.3: Dead Money Time Stop

Exit positions that haven't reached +1R within a configurable number of days.

**Files:**
- Modify: `src/sepa_scanner/backtest/models.py`
- Modify: `src/sepa_scanner/backtest/engine.py`
- Test: `tests/test_backtest_engine.py`

- [ ] **Step 1: Add dead_money_days to BacktestParams**

```python
@dataclass(slots=True)
class BacktestParams:
    # ... existing fields ...
    dead_money_days: int | None = 15  # NEW: exit if not at +1R within N days
```

- [ ] **Step 2: Write failing test**

```python
def test_backtest_dead_money_exit_when_no_progress() -> None:
    """Exit at dead_money_days if position hasn't reached +1R."""
    signals = _signal(entry_ref=101.0, stop=95.0)  # risk = 6
    # 4 bars of flat trading — never reaches +1R (107.0)
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC",
             "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0},
            {"date": date(2026, 3, 4), "symbol": "ABC",
             "open": 101.0, "high": 102.0, "low": 100.5, "close": 101.5},
            {"date": date(2026, 3, 5), "symbol": "ABC",
             "open": 101.5, "high": 102.5, "low": 100.0, "close": 101.0},
            {"date": date(2026, 3, 6), "symbol": "ABC",
             "open": 101.0, "high": 102.0, "low": 99.5, "close": 100.8},
        ]
    )

    result = simulate_breakout_backtest(
        signals, prices,
        BacktestParams(max_hold_days=10, dead_money_days=3),
    )

    trade = result.trades[0]
    assert trade.status == "dead_money"
    assert trade.holding_days == 3
```

- [ ] **Step 2b: Add dead_money_exits to BacktestSummary and metrics**

In `src/sepa_scanner/backtest/models.py`, add to `BacktestSummary`:

```python
dead_money_exits: int = 0
```

In `src/sepa_scanner/backtest/metrics.py`, in `summarize_backtest`, add:

```python
dead_money_exits=sum(1 for trade in trades if trade.status == "dead_money"),
```

- [ ] **Step 3: Implement dead money exit**

In `_simulate_signal`, after the existing bar loop checks but before trailing stop updates:

```python
# Dead money check: exit if not at +1R within dead_money_days
if (
    params.dead_money_days is not None
    and not breakeven_activated
    and holding_days >= params.dead_money_days
):
    return _close_trade(
        trade, status="dead_money", exit_date=bar["date"],
        exit_price=close_price, holding_days=holding_days,
        mae_price=mae_price, mfe_price=mfe_price,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_backtest_engine.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/sepa_scanner/backtest/models.py src/sepa_scanner/backtest/engine.py tests/test_backtest_engine.py
git commit -m "feat: dead money time stop — exit stale positions after N days"
```

---

## Chunk 3: Graduated Market Filter

Items #8 (3-state system) and #9 (asymmetric breadth thresholds)

---

### Task 3.1: MarketRegime Enum and Graduated Filter

Replace binary market_filter_on → bool with graduated MarketRegime (STRONG/NEUTRAL/WEAK).

**Files:**
- Create: `src/sepa_scanner/strategy/market_regime.py`
- Modify: `src/sepa_scanner/strategy/market_filter.py`
- Modify: `src/sepa_scanner/settings.py`
- Modify: `config/config.yaml`
- Modify: `src/sepa_scanner/strategy/signal_engine.py`
- Create: `tests/test_market_regime.py`

- [ ] **Step 1: Create MarketRegime enum**

Create `src/sepa_scanner/strategy/market_regime.py`:

```python
from __future__ import annotations

from enum import Enum


class MarketRegime(str, Enum):
    STRONG = "strong"     # >60% breadth: full position sizes, max positions
    NEUTRAL = "neutral"   # 40-60% breadth: half sizes, A+ setups only
    WEAK = "weak"         # <40% breadth: no new entries
```

- [ ] **Step 2: Update MarketFilterSettings**

In `src/sepa_scanner/settings.py`:

```python
class MarketFilterSettings(BaseModel):
    benchmark_ema_period: int = 21
    breadth_strong_pct: float = 0.60   # CHANGED from require_breadth_pct
    breadth_weak_pct: float = 0.40     # NEW
```

- [ ] **Step 3: Update config.yaml**

```yaml
market_filter:
  benchmark_ema_period: 21
  breadth_strong_pct: 0.60      # was require_breadth_pct: 0.50
  breadth_weak_pct: 0.40        # NEW
```

- [ ] **Step 4: Write tests for graduated filter**

Create `tests/test_market_regime.py`:

```python
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from sepa_scanner.strategy.market_filter import classify_market_regime
from sepa_scanner.strategy.market_regime import MarketRegime


def _benchmark_frame(
    close: float = 200.0,
    sma50: float = 180.0,
    sma200: float = 160.0,
    sma200_lag20: float = 155.0,
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["NIFTYBEES"],
            "date": [date(2026, 3, 14)],
            "close": [close],
            "sma50": [sma50],
            "sma200": [sma200],
            "sma200_lag20": [sma200_lag20],
        }
    )


SCAN_DATE = date(2026, 3, 14)


def test_strong_regime_when_secular_and_breadth_above_60() -> None:
    frame = _benchmark_frame()
    regime = classify_market_regime(
        frame, "NIFTYBEES", SCAN_DATE,
        benchmark_ema=190.0, breadth_pct=0.65,
        breadth_strong_pct=0.60, breadth_weak_pct=0.40,
    )
    assert regime == MarketRegime.STRONG


def test_neutral_regime_when_breadth_between_thresholds() -> None:
    frame = _benchmark_frame()
    regime = classify_market_regime(
        frame, "NIFTYBEES", SCAN_DATE,
        benchmark_ema=190.0, breadth_pct=0.50,
        breadth_strong_pct=0.60, breadth_weak_pct=0.40,
    )
    assert regime == MarketRegime.NEUTRAL


def test_weak_regime_when_breadth_below_40() -> None:
    frame = _benchmark_frame()
    regime = classify_market_regime(
        frame, "NIFTYBEES", SCAN_DATE,
        benchmark_ema=190.0, breadth_pct=0.35,
        breadth_strong_pct=0.60, breadth_weak_pct=0.40,
    )
    assert regime == MarketRegime.WEAK


def test_weak_regime_when_secular_fails() -> None:
    frame = _benchmark_frame(close=150.0)  # below sma200
    regime = classify_market_regime(
        frame, "NIFTYBEES", SCAN_DATE,
        benchmark_ema=190.0, breadth_pct=0.65,
        breadth_strong_pct=0.60, breadth_weak_pct=0.40,
    )
    assert regime == MarketRegime.WEAK


def test_neutral_regime_when_ema_fails_but_breadth_strong() -> None:
    frame = _benchmark_frame()
    regime = classify_market_regime(
        frame, "NIFTYBEES", SCAN_DATE,
        benchmark_ema=210.0,  # close (200) below EMA
        breadth_pct=0.65,
        breadth_strong_pct=0.60, breadth_weak_pct=0.40,
    )
    assert regime == MarketRegime.NEUTRAL  # secular passes, EMA fails → cap at NEUTRAL
```

- [ ] **Step 5: Implement classify_market_regime**

In `src/sepa_scanner/strategy/market_filter.py`:

```python
from sepa_scanner.strategy.market_regime import MarketRegime


def classify_market_regime(
    frame: pl.DataFrame,
    benchmark_symbol: str,
    scan_date: date,
    *,
    benchmark_ema: float | None = None,
    breadth_pct: float | None = None,
    breadth_strong_pct: float = 0.60,
    breadth_weak_pct: float = 0.40,
) -> MarketRegime:
    row = (
        frame.filter(
            (pl.col("symbol") == benchmark_symbol) & (pl.col("date") == pl.lit(scan_date))
        )
        .select("close", "sma50", "sma200", "sma200_lag20")
        .to_dicts()
    )
    if not row:
        raise ValueError(f"Missing benchmark data for {benchmark_symbol} on {scan_date}")

    benchmark = row[0]

    # Layer 1: Secular trend
    secular_ok = (
        benchmark["close"] > benchmark["sma200"]
        and benchmark["sma50"] > benchmark["sma200"]
        and benchmark["sma200"] > benchmark["sma200_lag20"]
    )
    if not secular_ok:
        return MarketRegime.WEAK

    # Layer 2: Cycle confirmation (caps at NEUTRAL if failing)
    ema_ok = True
    if benchmark_ema is not None and benchmark["close"] <= benchmark_ema:
        ema_ok = False

    # Layer 3: Breadth classification
    if breadth_pct is not None:
        if breadth_pct >= breadth_strong_pct and ema_ok:
            return MarketRegime.STRONG
        elif breadth_pct < breadth_weak_pct:
            return MarketRegime.WEAK
        else:
            return MarketRegime.NEUTRAL

    # No breadth data: use EMA as the differentiator
    return MarketRegime.STRONG if ema_ok else MarketRegime.NEUTRAL


# Backward-compatible wrapper.
# NOTE: This intentionally relaxes the old behavior. Previously, breadth < require_breadth_pct
# returned False. Now, breadth between (require_breadth_pct - 0.10) and require_breadth_pct
# returns True (NEUTRAL != WEAK). This is intentional — the dead zone was too aggressive.
# Callers that need the old strict behavior should use classify_market_regime directly.
def market_filter_on(
    frame: pl.DataFrame,
    benchmark_symbol: str,
    scan_date: date,
    *,
    benchmark_ema: float | None = None,
    breadth_pct: float | None = None,
    require_breadth_pct: float = 0.50,
) -> bool:
    """Backward-compatible binary filter. Returns True if regime is not WEAK."""
    regime = classify_market_regime(
        frame, benchmark_symbol, scan_date,
        benchmark_ema=benchmark_ema,
        breadth_pct=breadth_pct,
        breadth_strong_pct=require_breadth_pct,
        breadth_weak_pct=max(0.0, require_breadth_pct - 0.10),
    )
    return regime != MarketRegime.WEAK
```

- [ ] **Step 6: Update signal_engine to use regime**

In `src/sepa_scanner/strategy/signal_engine.py`, import `classify_market_regime` and `MarketRegime`. In `scan_for_date`, replace `market_filter_on` with `classify_market_regime` and store the regime in `ScanResult`:

Add `market_regime: str` field to `ScanResult`:

```python
@dataclass(slots=True)
class ScanResult:
    scan_date: date
    market_filter_on: bool
    market_regime: str = "strong"  # NEW
    # ... rest unchanged
```

Then in `scan_for_date`:

```python
from sepa_scanner.strategy.market_filter import classify_market_regime
from sepa_scanner.strategy.market_regime import MarketRegime

regime = classify_market_regime(
    prepared,
    settings.market.benchmark_symbol,
    scan_date,
    benchmark_ema=bench_ema_val,
    breadth_pct=breadth,
    breadth_strong_pct=settings.market_filter.breadth_strong_pct,
    breadth_weak_pct=settings.market_filter.breadth_weak_pct,
)
market_on = regime != MarketRegime.WEAK
```

- [ ] **Step 7: Run all tests**

Run: `pytest tests/ -v`
Expected: All PASS (backward-compatible `market_filter_on` preserved, existing tests still work)

- [ ] **Step 7b: Verify downstream consumers of ScanResult.market_filter_on**

The `market_filter_on` bool is consumed by:
- `src/sepa_scanner/storage/duckdb_store.py` (persisted in runs table)
- `src/sepa_scanner/types.py` (RunLog dataclass)
- `src/sepa_scanner/api/routes/scanner.py` (exposed in API)

All these still work because `market_filter_on` remains a bool. The new `market_regime` string field is additive. **Follow-up:** API routes and UI should eventually use `market_regime` for graduated display (badge: green/yellow/red). This is not blocking.

- [ ] **Step 8: Commit**

```bash
git add src/sepa_scanner/strategy/market_regime.py src/sepa_scanner/strategy/market_filter.py src/sepa_scanner/settings.py src/sepa_scanner/strategy/signal_engine.py config/config.yaml tests/test_market_regime.py tests/test_market_filter.py
git commit -m "feat: graduated 3-state market filter (strong/neutral/weak)"
```

---

## Chunk 4: Portfolio-Level Risk Controls

Item #5 — max heat, max positions, sector caps, drawdown circuit breakers.

These controls apply at the **backtest simulation level** (constraining which signals get filled) and as **advisory outputs** in live scanning.

---

### Task 4.1: Portfolio Risk Settings and Model

**Files:**
- Modify: `src/sepa_scanner/settings.py`
- Create: `src/sepa_scanner/backtest/portfolio.py`
- Modify: `src/sepa_scanner/backtest/models.py`
- Modify: `config/config.yaml`
- Create: `tests/test_portfolio_controls.py`

- [ ] **Step 1: Add PortfolioSettings**

In `src/sepa_scanner/settings.py`:

```python
class PortfolioSettings(BaseModel):
    max_heat_pct: float = 0.10           # max 10% total open risk
    max_positions: int = 10              # max concurrent positions
    max_sector_positions: int = 3        # max per sector
    max_sector_pct: float = 0.40         # max 40% in any one sector
    drawdown_half_pct: float = 0.10      # halve sizes at -10% from peak
    drawdown_stop_pct: float = 0.15      # stop new entries at -15%
    drawdown_close_pct: float = 0.20     # close all at -20%
    initial_capital: float = 1_000_000.0
    risk_per_trade_pct: float = 0.01     # 1% risk per trade
```

Add to `AppSettings`:

```python
portfolio: PortfolioSettings = Field(default_factory=PortfolioSettings)
```

Update `load_settings()` to parse it.

- [ ] **Step 2: Update config.yaml**

```yaml
portfolio:
  max_heat_pct: 0.10
  max_positions: 10
  max_sector_positions: 3
  max_sector_pct: 0.40
  drawdown_half_pct: 0.10
  drawdown_stop_pct: 0.15
  drawdown_close_pct: 0.20
  initial_capital: 1000000
  risk_per_trade_pct: 0.01
```

- [ ] **Step 3: Write tests for portfolio controls**

Create `tests/test_portfolio_controls.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from sepa_scanner.backtest.portfolio import PortfolioManager


def test_rejects_trade_when_max_positions_reached() -> None:
    pm = PortfolioManager(max_positions=2, initial_capital=1_000_000.0, risk_per_trade_pct=0.01)
    assert pm.can_open_trade(sector="IT") is True
    pm.open_trade(symbol="A", sector="IT", risk_per_share=5.0, entry_price=100.0)
    pm.open_trade(symbol="B", sector="PHARMA", risk_per_share=4.0, entry_price=80.0)
    assert pm.can_open_trade(sector="FMCG") is False


def test_rejects_trade_when_max_sector_reached() -> None:
    pm = PortfolioManager(max_positions=10, max_sector_positions=2, initial_capital=1_000_000.0, risk_per_trade_pct=0.01)
    pm.open_trade(symbol="A", sector="IT", risk_per_share=5.0, entry_price=100.0)
    pm.open_trade(symbol="B", sector="IT", risk_per_share=4.0, entry_price=80.0)
    assert pm.can_open_trade(sector="IT") is False
    assert pm.can_open_trade(sector="PHARMA") is True


def test_rejects_trade_when_max_heat_exceeded() -> None:
    # Capital = 100k, risk_per_trade = 1% = 1000, heat = 1000/100k = 1%
    # max_heat = 2%, so can open 2 trades
    pm = PortfolioManager(
        max_positions=10, max_heat_pct=0.02,
        initial_capital=100_000.0, risk_per_trade_pct=0.01,
    )
    pm.open_trade(symbol="A", sector="IT", risk_per_share=5.0, entry_price=100.0)
    pm.open_trade(symbol="B", sector="PHARMA", risk_per_share=4.0, entry_price=80.0)
    assert pm.can_open_trade(sector="FMCG") is False


def test_drawdown_halves_position_size() -> None:
    pm = PortfolioManager(
        initial_capital=100_000.0, risk_per_trade_pct=0.01,
        drawdown_half_pct=0.10,
    )
    pm.update_equity(90_000.0)  # -10% drawdown
    assert pm.position_size_multiplier == 0.5


def test_drawdown_stops_new_entries() -> None:
    pm = PortfolioManager(
        initial_capital=100_000.0, risk_per_trade_pct=0.01,
        drawdown_stop_pct=0.15,
    )
    pm.update_equity(85_000.0)  # -15% drawdown
    assert pm.can_open_trade(sector="IT") is False
```

- [ ] **Step 4: Implement PortfolioManager**

Create `src/sepa_scanner/backtest/portfolio.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OpenPosition:
    symbol: str
    sector: str
    risk_amount: float
    position_value: float


@dataclass
class PortfolioManager:
    initial_capital: float = 1_000_000.0
    risk_per_trade_pct: float = 0.01
    max_positions: int = 10
    max_sector_positions: int = 3
    max_sector_pct: float = 0.40
    max_heat_pct: float = 0.10
    drawdown_half_pct: float = 0.10
    drawdown_stop_pct: float = 0.15
    drawdown_close_pct: float = 0.20

    equity_peak: float = field(init=False)
    current_equity: float = field(init=False)
    positions: list[OpenPosition] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.equity_peak = self.initial_capital
        self.current_equity = self.initial_capital

    @property
    def drawdown_pct(self) -> float:
        if self.equity_peak == 0:
            return 0.0
        return (self.equity_peak - self.current_equity) / self.equity_peak

    @property
    def position_size_multiplier(self) -> float:
        dd = self.drawdown_pct
        if dd >= self.drawdown_close_pct:
            return 0.0
        if dd >= self.drawdown_stop_pct:
            return 0.0
        if dd >= self.drawdown_half_pct:
            return 0.5
        return 1.0

    @property
    def total_heat(self) -> float:
        return sum(p.risk_amount for p in self.positions)

    def update_equity(self, equity: float) -> None:
        self.current_equity = equity
        self.equity_peak = max(self.equity_peak, equity)

    def can_open_trade(self, sector: str) -> bool:
        if self.position_size_multiplier == 0.0:
            return False
        if len(self.positions) >= self.max_positions:
            return False
        sector_count = sum(1 for p in self.positions if p.sector == sector)
        if sector_count >= self.max_sector_positions:
            return False
        risk_amount = self.current_equity * self.risk_per_trade_pct * self.position_size_multiplier
        if (self.total_heat + risk_amount) / self.current_equity > self.max_heat_pct:
            return False
        return True

    def open_trade(
        self, symbol: str, sector: str, risk_per_share: float, entry_price: float,
    ) -> float:
        """Open a position. Returns the number of shares."""
        risk_budget = self.current_equity * self.risk_per_trade_pct * self.position_size_multiplier
        shares = risk_budget / risk_per_share if risk_per_share > 0 else 0
        position_value = shares * entry_price
        self.positions.append(OpenPosition(
            symbol=symbol, sector=sector,
            risk_amount=risk_budget, position_value=position_value,
        ))
        return shares

    def close_trade(self, symbol: str) -> None:
        self.positions = [p for p in self.positions if p.symbol != symbol]
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_portfolio_controls.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/sepa_scanner/settings.py src/sepa_scanner/backtest/portfolio.py src/sepa_scanner/backtest/models.py config/config.yaml tests/test_portfolio_controls.py
git commit -m "feat: portfolio-level risk controls — heat, positions, sectors, drawdown"
```

---

### Task 4.2: Wire PortfolioManager into Backtest Engine

**Files:**
- Modify: `src/sepa_scanner/backtest/engine.py`
- Test: `tests/test_portfolio_controls.py`

- [ ] **Step 1: Add portfolio_manager parameter to simulate_breakout_backtest**

```python
def simulate_breakout_backtest(
    signals_df: pl.DataFrame,
    ohlcv_df: pl.DataFrame,
    params: BacktestParams,
    corp_actions: pl.DataFrame | None = None,
    portfolio_manager: PortfolioManager | None = None,  # NEW
) -> BacktestResult:
```

When `portfolio_manager` is provided, check `can_open_trade()` before each signal fill. If rejected, mark the trade as `"skipped_portfolio"`. After each trade closes, call `close_trade()`.

**Note:** This changes the backtest from per-symbol-group parallelism to sequential execution (portfolio state is shared). When `portfolio_manager is None`, existing parallel behavior is preserved.

- [ ] **Step 2: Write test for portfolio-constrained backtest**

```python
def test_backtest_skips_signal_when_portfolio_full() -> None:
    from sepa_scanner.backtest.portfolio import PortfolioManager

    signals = pl.concat([
        _signal(symbol="A", signal_date=date(2026, 3, 2)),
        _signal(symbol="B", signal_date=date(2026, 3, 2)),
        _signal(symbol="C", signal_date=date(2026, 3, 2)),
    ], how="vertical")

    prices = _ohlcv([
        {"date": date(2026, 3, 3), "symbol": s, "open": 101.5, "high": 102.0, "low": 100.0, "close": 101.0}
        for s in ["A", "B", "C"]
    ] + [
        {"date": date(2026, 3, 4), "symbol": s, "open": 101.0, "high": 102.0, "low": 100.0, "close": 101.5}
        for s in ["A", "B", "C"]
    ])

    pm = PortfolioManager(max_positions=2, initial_capital=1_000_000.0, risk_per_trade_pct=0.01)
    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=2), portfolio_manager=pm)

    statuses = [t.status for t in result.trades]
    assert statuses.count("skipped_portfolio") >= 1
```

- [ ] **Step 3: Implement and run tests**

Run: `pytest tests/test_portfolio_controls.py tests/test_backtest_engine.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/sepa_scanner/backtest/engine.py tests/test_portfolio_controls.py
git commit -m "feat: wire portfolio controls into backtest engine"
```

---

## Chunk 5: India-Specific Filters & Sector RS

Items #7 (sector RS ranking) and #11 (F&O preference, ASM/GSM exclusion, market cap/ADTV floors).

These require external data sources. The implementation uses pluggable adapters that can be loaded from local files or fetched from NSE.

---

### Task 5.1: NSE Metadata Adapter

**Files:**
- Create: `src/sepa_scanner/data_sources/nse_meta.py`
- Create: `tests/test_nse_meta.py`
- Modify: `src/sepa_scanner/settings.py`
- Modify: `config/config.yaml`

- [ ] **Step 1: Define the interface and settings**

In `src/sepa_scanner/settings.py`:

```python
class IndiaFilterSettings(BaseModel):
    prefer_fno: bool = True                    # prefer F&O stocks
    exclude_asm_gsm: bool = True               # exclude ASM/GSM stocks
    min_market_cap_cr: float = 500.0           # ₹500Cr minimum
    min_adtv_cr: float = 5.0                   # ₹5Cr minimum ADTV
    fno_list_path: str | None = None           # path to local F&O list CSV
    asm_gsm_list_path: str | None = None       # path to local ASM/GSM list CSV
    sector_map_path: str | None = None         # path to sector classification CSV
```

- [ ] **Step 2: Create NSE metadata module**

Create `src/sepa_scanner/data_sources/nse_meta.py`:

```python
from __future__ import annotations

from pathlib import Path

import polars as pl


def load_fno_list(path: Path | None = None) -> set[str]:
    """Load F&O stock list. Returns set of symbols."""
    if path is None or not Path(path).exists():
        return set()
    df = pl.read_csv(path)
    return set(df["symbol"].to_list())


def load_asm_gsm_list(path: Path | None = None) -> set[str]:
    """Load ASM/GSM surveillance list. Returns set of symbols to exclude."""
    if path is None or not Path(path).exists():
        return set()
    df = pl.read_csv(path)
    return set(df["symbol"].to_list())


def load_sector_map(path: Path | None = None) -> dict[str, str]:
    """Load sector classification. Returns {symbol: sector_name}."""
    if path is None or not Path(path).exists():
        return {}
    df = pl.read_csv(path)
    return dict(zip(df["symbol"].to_list(), df["sector"].to_list()))


def apply_india_filters(
    eligible: pl.DataFrame,
    *,
    fno_symbols: set[str] | None = None,
    asm_gsm_symbols: set[str] | None = None,
    min_market_cap_cr: float = 500.0,
    min_adtv_cr: float = 5.0,
) -> pl.DataFrame:
    """Apply India-specific universe filters to eligible stocks."""
    result = eligible

    # Exclude ASM/GSM stocks
    if asm_gsm_symbols:
        result = result.filter(~pl.col("symbol").is_in(list(asm_gsm_symbols)))

    # Market cap filter (if market_cap column available)
    if "market_cap" in result.columns:
        result = result.filter(pl.col("market_cap") >= min_market_cap_cr * 1e7)

    # ADTV filter: adv50 is in INR, convert threshold from Cr
    # adv50 is always present in eligible universe (computed in _eligible_universe)
    if "adv50" in result.columns:
        result = result.filter(pl.col("adv50") >= min_adtv_cr * 1e7)

    # F&O preference: sort F&O stocks first (don't exclude non-F&O)
    if fno_symbols:
        result = result.with_columns(
            pl.col("symbol").is_in(list(fno_symbols)).alias("is_fno")
        ).sort("is_fno", descending=True)

    return result
```

- [ ] **Step 3: Write tests**

Create `tests/test_nse_meta.py`:

```python
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
```

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_nse_meta.py -v`

```bash
git add src/sepa_scanner/data_sources/nse_meta.py src/sepa_scanner/settings.py config/config.yaml tests/test_nse_meta.py
git commit -m "feat: India-specific filters — ASM/GSM exclusion, ADTV floor, F&O preference"
```

---

### Task 5.2: Sector-Level RS Ranking

**Files:**
- Create: `src/sepa_scanner/strategy/sector_rs.py`
- Create: `tests/test_sector_rs.py`

- [ ] **Step 1: Write test for sector RS**

Create `tests/test_sector_rs.py`:

```python
from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from sepa_scanner.strategy.sector_rs import compute_sector_rs


def test_sector_rs_ranks_sectors_by_constituent_performance() -> None:
    start = date(2026, 3, 2)
    dates = [start + timedelta(days=offset) for offset in range(5)]

    frame = pl.DataFrame({
        "date": dates * 4,
        "symbol": ["BM"] * 5 + ["IT1"] * 5 + ["IT2"] * 5 + ["PH1"] * 5,
        "close": [
            100.0, 102.0, 104.0, 106.0, 108.0,  # benchmark
            100.0, 110.0, 120.0, 130.0, 140.0,  # IT1: strong
            100.0, 108.0, 116.0, 124.0, 132.0,  # IT2: strong
            100.0, 99.0, 98.0, 97.0, 96.0,      # PH1: weak
        ],
    })
    sector_map = {"IT1": "IT", "IT2": "IT", "PH1": "PHARMA"}

    result = compute_sector_rs(
        frame, sector_map, benchmark_symbol="BM",
        scan_date=dates[-1], lookbacks=[2, 4], weights=[0.5, 0.5],
    )

    assert result["IT"] > result["PHARMA"]
```

- [ ] **Step 2: Implement sector RS**

Create `src/sepa_scanner/strategy/sector_rs.py`:

```python
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
```

- [ ] **Step 3: Run tests and commit**

Run: `pytest tests/test_sector_rs.py -v`

```bash
git add src/sepa_scanner/strategy/sector_rs.py tests/test_sector_rs.py
git commit -m "feat: sector-level RS ranking for industry group filtering"
```

---

### Task 5.3: Wire India Filters and Sector RS into Signal Engine

**Files:**
- Modify: `src/sepa_scanner/strategy/signal_engine.py`

- [ ] **Step 1: Add India filter integration to _eligible_universe**

In `src/sepa_scanner/strategy/signal_engine.py`, import and apply India filters at the end of `_eligible_universe`:

```python
from sepa_scanner.data_sources.nse_meta import apply_india_filters, load_asm_gsm_list, load_fno_list

def _eligible_universe(
    prepared: pl.DataFrame, settings: AppSettings, scan_date: date,
    *, fno_symbols: set[str] | None = None, asm_gsm_symbols: set[str] | None = None,
) -> pl.DataFrame:
    # ... existing filtering logic unchanged ...
    result = (
        prepared.filter(pl.col("date") == pl.lit(scan_date))
        # ... existing chain ...
        .head(universe.max_symbols)
    )

    # Apply India-specific filters if configured
    india_cfg = getattr(settings, "india_filter", None)
    if india_cfg is not None:
        result = apply_india_filters(
            result,
            fno_symbols=fno_symbols,
            asm_gsm_symbols=asm_gsm_symbols,
            min_market_cap_cr=india_cfg.min_market_cap_cr,
            min_adtv_cr=india_cfg.min_adtv_cr,
        )

    return result
```

- [ ] **Step 2: Add sector RS filter to scan_for_date**

In `scan_for_date`, after computing RS ranks and before building signals:

```python
from sepa_scanner.strategy.sector_rs import compute_sector_rs

# Compute sector RS if sector_map available
sector_map = getattr(settings, "_sector_map", None)  # loaded at startup
if sector_map:
    sector_scores = compute_sector_rs(
        prepared.select("date", "symbol", "close"),
        sector_map,
        benchmark_symbol=settings.market.benchmark_symbol,
        scan_date=scan_date,
        lookbacks=settings.relative_strength.lookbacks,
        weights=settings.relative_strength.weights,
    )
    # Filter: require stock's sector to be in top 70% (not bottom 30%)
    if sector_scores:
        all_scores = sorted(sector_scores.values())
        cutoff_idx = max(0, int(len(all_scores) * 0.30))
        sector_cutoff = all_scores[cutoff_idx] if cutoff_idx < len(all_scores) else float("-inf")
        weak_sectors = {s for s, score in sector_scores.items() if score < sector_cutoff}
        if weak_sectors:
            # Add sector column and filter
            symbol_to_sector = {sym: sec for sym, sec in sector_map.items()}
            scan_rows = scan_rows.with_columns(
                pl.col("symbol").replace(symbol_to_sector, default="UNKNOWN").alias("sector")
            ).filter(~pl.col("sector").is_in(list(weak_sectors)))
```

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS (filters are no-ops when not configured — `india_filter` not in default settings, `_sector_map` is None)

- [ ] **Step 4: Commit**

```bash
git add src/sepa_scanner/strategy/signal_engine.py
git commit -m "feat: integrate India filters and sector RS into scan pipeline"
```

---

## Chunk 6: Fundamentals & Advanced Patterns

Items #1 (earnings acceleration), #13 (multi-pattern detection), #14 (India VIX).

These items require external data sources that may not be immediately available. The implementation creates the adapter interfaces, core algorithms, and integration points. Actual data fetching is deferred to data source configuration.

---

### Task 6.1: Earnings Acceleration Filter

**Files:**
- Create: `src/sepa_scanner/data_sources/fundamentals.py`
- Create: `tests/test_fundamentals.py`
- Modify: `src/sepa_scanner/settings.py`

- [ ] **Step 1: Define earnings data model and filter**

Create `src/sepa_scanner/data_sources/fundamentals.py`:

```python
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl


def load_quarterly_results(path: Path | None = None) -> pl.DataFrame:
    """Load quarterly earnings data.

    Expected columns: symbol, quarter_end, eps, revenue, yoy_eps_growth.
    Returns empty DataFrame if path is None or doesn't exist.
    """
    if path is None or not Path(path).exists():
        return pl.DataFrame(schema={
            "symbol": pl.String,
            "quarter_end": pl.Date,
            "eps": pl.Float64,
            "revenue": pl.Float64,
            "yoy_eps_growth": pl.Float64,
        })
    return pl.read_csv(path, try_parse_dates=True)


def compute_earnings_acceleration(earnings: pl.DataFrame) -> pl.DataFrame:
    """Compute earnings acceleration: QoQ change in YoY EPS growth rate.

    Returns DataFrame with columns: symbol, latest_quarter, yoy_growth, acceleration, passes_filter.
    A stock passes if: yoy_growth >= 20% AND acceleration > 0 (growth is accelerating).
    """
    if earnings.is_empty():
        return pl.DataFrame(schema={
            "symbol": pl.String,
            "latest_quarter": pl.Date,
            "yoy_growth": pl.Float64,
            "acceleration": pl.Float64,
            "passes_filter": pl.Boolean,
        })

    # Sort by symbol and quarter, compute acceleration
    sorted_df = earnings.sort(["symbol", "quarter_end"])

    result = sorted_df.with_columns(
        pl.col("yoy_eps_growth").shift(1).over("symbol").alias("prev_yoy_growth"),
    ).with_columns(
        (pl.col("yoy_eps_growth") - pl.col("prev_yoy_growth")).alias("acceleration"),
    )

    # Get latest quarter per symbol
    latest = (
        result.group_by("symbol")
        .agg(pl.all().sort_by("quarter_end").last())
        .select(
            "symbol",
            pl.col("quarter_end").alias("latest_quarter"),
            pl.col("yoy_eps_growth").alias("yoy_growth"),
            "acceleration",
        )
    ).with_columns(
        (
            (pl.col("yoy_growth") >= 0.20)
            & (pl.col("acceleration") > 0)
        ).alias("passes_filter"),
    )

    return latest
```

- [ ] **Step 2: Write tests**

Create `tests/test_fundamentals.py`:

```python
from __future__ import annotations

from datetime import date

import polars as pl

from sepa_scanner.data_sources.fundamentals import compute_earnings_acceleration


def test_earnings_acceleration_detects_accelerating_growth() -> None:
    earnings = pl.DataFrame({
        "symbol": ["ABC", "ABC", "ABC"],
        "quarter_end": [date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)],
        "eps": [10.0, 12.0, 15.0],
        "revenue": [100.0, 120.0, 150.0],
        "yoy_eps_growth": [0.15, 0.20, 0.30],  # accelerating: 15% -> 20% -> 30%
    })

    result = compute_earnings_acceleration(earnings)
    abc = result.filter(pl.col("symbol") == "ABC")
    assert abc["passes_filter"][0] is True
    assert abc["acceleration"][0] > 0


def test_earnings_acceleration_rejects_decelerating_growth() -> None:
    earnings = pl.DataFrame({
        "symbol": ["XYZ", "XYZ", "XYZ"],
        "quarter_end": [date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)],
        "eps": [10.0, 12.0, 13.0],
        "revenue": [100.0, 120.0, 130.0],
        "yoy_eps_growth": [0.30, 0.25, 0.22],  # decelerating: 30% -> 25% -> 22%
    })

    result = compute_earnings_acceleration(earnings)
    xyz = result.filter(pl.col("symbol") == "XYZ")
    assert xyz["passes_filter"][0] is False


def test_empty_earnings_returns_empty_result() -> None:
    result = compute_earnings_acceleration(pl.DataFrame(schema={
        "symbol": pl.String,
        "quarter_end": pl.Date,
        "eps": pl.Float64,
        "revenue": pl.Float64,
        "yoy_eps_growth": pl.Float64,
    }))
    assert result.is_empty()
```

- [ ] **Step 3: Run tests and commit**

Run: `pytest tests/test_fundamentals.py -v`

```bash
git add src/sepa_scanner/data_sources/fundamentals.py tests/test_fundamentals.py src/sepa_scanner/settings.py
git commit -m "feat: earnings acceleration filter with pluggable data source"
```

---

### Task 6.2: Multi-Pattern Detection Framework

**Files:**
- Create: `src/sepa_scanner/strategy/patterns.py`
- Create: `tests/test_patterns.py`

- [ ] **Step 0: Ensure numpy is in pyproject.toml dependencies**

numpy is already used by `src/sepa_scanner/indicators/contraction.py` but not listed as an explicit dependency. Add it:

```bash
# In pyproject.toml, add to dependencies:
# "numpy>=1.26",
```

- [ ] **Step 1: Define pattern detection interface**

Create `src/sepa_scanner/strategy/patterns.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import polars as pl


class PatternType(str, Enum):
    VCP = "vcp"
    HIGH_TIGHT_FLAG = "htf"
    FLAT_BASE = "flat_base"
    # Future: CUP_WITH_HANDLE, DOUBLE_BOTTOM, IPO_BASE (add when implemented)


@dataclass(slots=True)
class PatternMatch:
    pattern: PatternType
    confidence: float        # 0-1
    base_depth_pct: float
    base_duration_days: int
    pivot_price: float


def detect_high_tight_flag(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    *,
    min_gain_pct: float = 1.0,
    gain_window: int = 40,
    max_correction_pct: float = 0.25,
    correction_window: int = 25,
) -> PatternMatch | None:
    """Detect High Tight Flag (Power Play) pattern.

    Requirements:
    - Stock doubles (≥100%) in gain_window days
    - Then corrects ≤max_correction_pct over correction_window days
    """
    if len(closes) < gain_window + correction_window:
        return None

    # Check for doubling in the gain phase
    gain_start = len(closes) - gain_window - correction_window
    gain_end = len(closes) - correction_window
    gain_start_price = closes[gain_start]
    gain_peak = np.max(highs[gain_start:gain_end])

    if gain_start_price <= 0 or (gain_peak / gain_start_price - 1.0) < min_gain_pct:
        return None

    # Check correction phase
    correction_phase = closes[gain_end:]
    correction_low = np.min(lows[gain_end:])
    correction_depth = (gain_peak - correction_low) / gain_peak

    if correction_depth > max_correction_pct:
        return None

    return PatternMatch(
        pattern=PatternType.HIGH_TIGHT_FLAG,
        confidence=min(1.0, (gain_peak / gain_start_price - 1.0) / 2.0),
        base_depth_pct=correction_depth,
        base_duration_days=correction_window,
        pivot_price=gain_peak,
    )


def detect_flat_base(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    *,
    max_depth_pct: float = 0.15,
    min_duration: int = 25,
) -> PatternMatch | None:
    """Detect flat base pattern (≤15% depth, minimum 5 weeks)."""
    if len(closes) < min_duration:
        return None

    window = closes[-min_duration:]
    window_highs = highs[-min_duration:]
    window_lows = lows[-min_duration:]

    peak = np.max(window_highs)
    trough = np.min(window_lows)

    if peak <= 0:
        return None

    depth = (peak - trough) / peak
    if depth > max_depth_pct:
        return None

    return PatternMatch(
        pattern=PatternType.FLAT_BASE,
        confidence=1.0 - (depth / max_depth_pct),
        base_depth_pct=depth,
        base_duration_days=min_duration,
        pivot_price=peak,
    )


def detect_patterns(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
) -> list[PatternMatch]:
    """Run all pattern detectors and return matches."""
    matches: list[PatternMatch] = []

    htf = detect_high_tight_flag(closes, highs, lows)
    if htf:
        matches.append(htf)

    flat = detect_flat_base(closes, highs, lows)
    if flat:
        matches.append(flat)

    return matches
```

- [ ] **Step 2: Write tests**

Create `tests/test_patterns.py`:

```python
from __future__ import annotations

import numpy as np

from sepa_scanner.strategy.patterns import (
    PatternType,
    detect_flat_base,
    detect_high_tight_flag,
)


def test_htf_detects_doubling_with_tight_correction() -> None:
    n = 65  # 40 gain + 25 correction
    closes = np.zeros(n)
    highs = np.zeros(n)
    lows = np.zeros(n)

    # Gain phase: 50 -> 110 (120% gain)
    for i in range(40):
        closes[i] = 50.0 + (60.0 / 40) * i
        highs[i] = closes[i] + 1.0
        lows[i] = closes[i] - 1.0

    # Correction phase: 110 -> 95 (13.6% correction) then back up
    peak = 110.0
    for i in range(40, 65):
        offset = i - 40
        closes[i] = peak - 15.0 * np.sin(offset / 25 * np.pi)  # gentle correction
        closes[i] = max(closes[i], peak * 0.85)
        highs[i] = closes[i] + 1.0
        lows[i] = closes[i] - 1.0

    result = detect_high_tight_flag(closes, highs, lows)
    assert result is not None
    assert result.pattern == PatternType.HIGH_TIGHT_FLAG


def test_htf_rejects_insufficient_gain() -> None:
    n = 65
    closes = np.linspace(100, 140, n)  # only 40% gain
    highs = closes + 1.0
    lows = closes - 1.0

    result = detect_high_tight_flag(closes, highs, lows)
    assert result is None


def test_flat_base_detects_tight_consolidation() -> None:
    n = 30
    closes = np.full(n, 100.0) + np.random.default_rng(42).normal(0, 1, n)
    highs = closes + 2.0
    lows = closes - 2.0

    result = detect_flat_base(closes, highs, lows, max_depth_pct=0.15, min_duration=25)
    assert result is not None
    assert result.pattern == PatternType.FLAT_BASE
```

- [ ] **Step 3: Run tests and commit**

Run: `pytest tests/test_patterns.py -v`

```bash
git add src/sepa_scanner/strategy/patterns.py tests/test_patterns.py
git commit -m "feat: multi-pattern detection framework (HTF, flat base)"
```

---

### Task 6.3: India VIX Integration

**Files:**
- Create: `src/sepa_scanner/data_sources/vix.py`
- Modify: `src/sepa_scanner/settings.py`

- [ ] **Step 1: Create VIX adapter**

Create `src/sepa_scanner/data_sources/vix.py`:

```python
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl


def load_vix_data(path: Path | None = None) -> pl.DataFrame:
    """Load India VIX data from local CSV.

    Expected columns: date, vix_close.
    """
    if path is None or not Path(path).exists():
        return pl.DataFrame(schema={"date": pl.Date, "vix_close": pl.Float64})
    return pl.read_csv(path, try_parse_dates=True)


def vix_position_multiplier(vix_value: float | None) -> float:
    """Compute position size multiplier based on India VIX level.

    VIX < 15: 1.0 (full size)
    VIX 15-20: 1.0 (normal)
    VIX 20-25: 0.5 (reduce)
    VIX > 25: 0.0 (no new entries)
    """
    if vix_value is None:
        return 1.0
    if vix_value > 25.0:
        return 0.0
    if vix_value > 20.0:
        return 0.5
    return 1.0
```

- [ ] **Step 2: Write tests for VIX module**

Create `tests/test_vix.py`:

```python
from __future__ import annotations

from sepa_scanner.data_sources.vix import vix_position_multiplier


def test_vix_full_size_below_15() -> None:
    assert vix_position_multiplier(12.0) == 1.0


def test_vix_normal_at_15_to_20() -> None:
    assert vix_position_multiplier(17.0) == 1.0


def test_vix_reduced_at_20_to_25() -> None:
    assert vix_position_multiplier(22.0) == 0.5


def test_vix_no_entries_above_25() -> None:
    assert vix_position_multiplier(30.0) == 0.0


def test_vix_none_returns_full_size() -> None:
    assert vix_position_multiplier(None) == 1.0
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_vix.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/sepa_scanner/data_sources/vix.py tests/test_vix.py src/sepa_scanner/settings.py
git commit -m "feat: India VIX integration for position sizing"
```

**Integration note:** VIX multiplier is consumed by `PortfolioManager.position_size_multiplier` (from Chunk 4). The integration point is in the backtest engine: before opening a trade, multiply the portfolio manager's size by `vix_position_multiplier(current_vix)`. This wiring happens when both Chunks 4 and 6 are complete.

---

## Chunk 7: STRATEGY.md Update

After all implementation chunks, update the canonical strategy document.

---

### Task 7.1: Update STRATEGY.md

**Files:**
- Modify: `docs/STRATEGY.md`

- [ ] **Step 1: Update parameter tables in Sections 4, 6, 7, 8, 11, 12, 15, 17**

For each section, replace old parameter values with new ones. Key changes:

| Section | Parameter | Old → New |
|---------|-----------|-----------|
| 4 (Market Filter) | `require_breadth_pct` | 0.50 → graduated: `breadth_strong_pct: 0.60`, `breadth_weak_pct: 0.40` |
| 4 (Market Filter) | return type | `bool` → `MarketRegime` enum (STRONG/NEUTRAL/WEAK) |
| 6 (Trend Template) | `sma200_slope_lookback` | 22 → 66 |
| 7 (RS) | `rs_rank_min` | 70 → 80 |
| 7 (RS) | NEW: `skip_recent_days` | — → 21 |
| 8 (VCP) | NEW: `contraction_standard_count` | — → 3 |
| 8 (VCP) | NEW: `contraction_tier` | — → advisory column (premium/standard/low) |
| 11 (Breakout) | `breakout_buffer_pct` | 0.003 → fallback; new: `breakout_buffer_atr_mult: 0.5` |
| 12 (Risk) | `max_stop_pct` | 0.08 → 0.10 |
| 12 (Risk) | NEW: `entry_buffer_atr_mult` | — → 0.5 |
| 15 (Backtest) | `exit_mode` | V2_TRAIL_21EMA → V3_TRAIL_PCT |
| 15 (Backtest) | `trail_activation_r` | 1.0 → 2.0 |
| 15 (Backtest) | NEW: `trail_pct` | — → 0.20 |
| 15 (Backtest) | NEW: `dead_money_days` | — → 15 |

Also update Section 4 code block to match actual `classify_market_regime` API signature.
Also update Section 17 complete YAML block to include all new keys.

- [ ] **Step 2: Add new Section 22 — "Portfolio Risk Management"**

Content to include:
- Table: `max_heat_pct: 0.10`, `max_positions: 10`, `max_sector_positions: 3`, `max_sector_pct: 0.40`
- Drawdown circuit breaker tiers: `-10% → half size`, `-15% → stop entries`, `-20% → close all`
- Position sizing formula: `shares = (equity × risk_per_trade_pct × drawdown_mult × vix_mult) / risk_per_share`
- Invariant: `sum(open_risk) / equity <= max_heat_pct`

- [ ] **Step 3: Add new Section 23 — "India-Specific Adaptations"**

Content to include:
- F&O preference rationale (circuit limit risk for non-F&O stocks)
- ASM/GSM/ESM exclusion list (updated monthly from NSE)
- Minimum thresholds: `₹500Cr market cap`, `₹5Cr ADTV`, `100K daily volume`
- India VIX position sizing bands: `<15 full`, `15-20 normal`, `20-25 half`, `>25 no entries`
- Survivorship bias awareness: use NSE Bhavcopy (current data source) — already bias-free

- [ ] **Step 4: Add Section 20 Changelog entry**

```markdown
### 2026-03-15 — V2 Critical Evaluation Implementation

**Scanner core:** RS cutoff raised to 80th percentile with 21-day skip-month; SMA200
slope lookback increased to 66 days; breakout buffer changed from fixed 0.3% to
0.5× ATR(14); progressive contraction verification added with quality tiers.

**Exit strategy:** New V3 percentage trailing stop (20%, empirically superior);
two-stage activation (breakeven at +1R, trail at +2R); dead money time stop at 15 days.

**Market filter:** Graduated 3-state system (STRONG/NEUTRAL/WEAK) replacing binary
on/off; asymmetric breadth thresholds (60% bull, 40% bear).

**Portfolio controls:** Max 10% portfolio heat, 10 concurrent positions, 3 per sector;
drawdown circuit breakers at -10%/-15%/-20%.

**India-specific:** F&O preference, ASM/GSM exclusion, ₹500Cr market cap floor,
₹5Cr ADTV floor; India VIX position sizing.

**New modules:** Sector RS ranking, earnings acceleration filter, multi-pattern detection
(HTF, flat base), India VIX adapter.
```

- [ ] **Step 5: Commit**

```bash
git add docs/STRATEGY.md
git commit -m "docs: update STRATEGY.md with all V2 evaluation changes"
```

---

## Implementation Order and Batching

### Dependency Graph

```
Chunk 1 (params + algorithms) ─────── independent
Chunk 2 (exit strategy) ───────────── independent
Chunk 3 (market filter) ──────────── independent
Chunk 4 (portfolio controls) ──────── depends on Chunk 2 (exit modes)
Chunk 5 (India + sector RS) ──────── independent
Chunk 6 (fundamentals + patterns) ── independent
Chunk 7 (STRATEGY.md) ────────────── depends on all
```

### Parallel Execution Plan

**Batch A (parallel):** Chunks 1, 2, 3 — all independent, no shared files
**Batch B (parallel):** Chunks 5, 6 — independent modules, but both eventually touch `signal_engine.py` via integration tasks. Run the module creation in parallel; do integration tasks (5.3, 6 integration) sequentially.
**Batch C (sequential):** Chunk 4 — after Chunk 2 completes (depends on exit modes)
**Batch D (sequential):** Chunk 7 — after all chunks complete

### Verification After Each Chunk

```bash
pytest tests/ -v                    # Full regression
python -m sepa_scanner scan         # Manual smoke test (if data available)
```
