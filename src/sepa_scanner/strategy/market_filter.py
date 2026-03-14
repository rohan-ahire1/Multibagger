from __future__ import annotations

from datetime import date

import polars as pl

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
