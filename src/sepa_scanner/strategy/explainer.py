"""Signal explainer — translates quantitative rules into human-readable reasoning.

Every signal decision is driven by explicit, quantitative rules.  This module
takes the per-condition diagnostics (from ``compute_symbol_diagnostics``) plus
the stored signal/watchlist row and produces structured explanations:

* **Condition checklist** — pass/fail with actual values vs thresholds
* **Signal narrative** — a template-driven paragraph telling the stock's "story"
* **Risk flags** — borderline conditions that passed but warrant caution
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sepa_scanner.settings import AppSettings


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ConditionResult:
    name: str
    passed: bool
    actual: str
    threshold: str
    explanation: str


@dataclass(slots=True)
class GateResult:
    name: str
    passed_count: int
    total_count: int
    conditions: list[ConditionResult]


@dataclass(slots=True)
class RiskFlag:
    message: str


@dataclass(slots=True)
class SignalExplanation:
    narrative: str
    gates: list[GateResult]
    risk_flags: list[RiskFlag]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_price(value: float | None) -> str:
    if value is None or value == 0:
        return "—"
    return f"\u20b9{value:,.2f}"


def _fmt_pct(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{decimals}f}%"


def _fmt_ratio(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}x"


def _safe_float(d: dict[str, Any], key: str, default: float = 0.0) -> float:
    v = d.get(key)
    if v is None:
        return default
    return float(v)


def _safe_bool(d: dict[str, Any], key: str) -> bool:
    return bool(d.get(key))


# ---------------------------------------------------------------------------
# Gate explainers
# ---------------------------------------------------------------------------

def explain_trend_template(
    diag: dict[str, Any], settings: AppSettings
) -> GateResult:
    cfg = settings.trend_template
    close = _safe_float(diag, "close")
    sma50 = _safe_float(diag, "sma50")
    sma150 = _safe_float(diag, "sma150")
    sma200 = _safe_float(diag, "sma200")
    sma200_lag = _safe_float(diag, "sma200_lag20")
    high52 = _safe_float(diag, "high52")
    low52 = _safe_float(diag, "low52")

    min_above_low = low52 * (1.0 + cfg.min_above_52w_low_pct)
    max_below_high = high52 * (1.0 - cfg.max_below_52w_high_pct)

    conditions = [
        ConditionResult(
            name="Close above SMA150",
            passed=_safe_bool(diag, "tt_close_above_sma150"),
            actual=_fmt_price(close),
            threshold=f"> {_fmt_price(sma150)}",
            explanation="Price above 150-day average confirms medium-term uptrend.",
        ),
        ConditionResult(
            name="Close above SMA200",
            passed=_safe_bool(diag, "tt_close_above_sma200"),
            actual=_fmt_price(close),
            threshold=f"> {_fmt_price(sma200)}",
            explanation="Price above 200-day average confirms long-term uptrend.",
        ),
        ConditionResult(
            name="SMA150 above SMA200",
            passed=_safe_bool(diag, "tt_sma150_above_sma200"),
            actual=_fmt_price(sma150),
            threshold=f"> {_fmt_price(sma200)}",
            explanation="Faster average above slower confirms bullish trend structure.",
        ),
        ConditionResult(
            name="SMA200 rising",
            passed=_safe_bool(diag, "tt_sma200_rising"),
            actual=f"{_fmt_price(sma200)} vs {_fmt_price(sma200_lag)} {cfg.sma200_slope_lookback}d ago",
            threshold="Rising",
            explanation="Rising 200-day average shows sustained buying interest.",
        ),
        ConditionResult(
            name="SMA50 above SMA150",
            passed=_safe_bool(diag, "tt_sma50_above_sma150"),
            actual=_fmt_price(sma50),
            threshold=f"> {_fmt_price(sma150)}",
            explanation="Short-term momentum leading the medium-term trend.",
        ),
        ConditionResult(
            name="SMA50 above SMA200",
            passed=_safe_bool(diag, "tt_sma50_above_sma200"),
            actual=_fmt_price(sma50),
            threshold=f"> {_fmt_price(sma200)}",
            explanation="Short-term momentum leading the long-term trend.",
        ),
        ConditionResult(
            name="Close above SMA50",
            passed=_safe_bool(diag, "tt_close_above_sma50"),
            actual=_fmt_price(close),
            threshold=f"> {_fmt_price(sma50)}",
            explanation="Price above 50-day average shows immediate momentum.",
        ),
        ConditionResult(
            name=f"Above {cfg.min_above_52w_low_pct:.0%} of 52w low",
            passed=_safe_bool(diag, "tt_above_52w_low"),
            actual=_fmt_price(close),
            threshold=f">= {_fmt_price(min_above_low)}",
            explanation="Established uptrend — not bottom-fishing.",
        ),
        ConditionResult(
            name=f"Within {cfg.max_below_52w_high_pct:.0%} of 52w high",
            passed=_safe_bool(diag, "tt_near_52w_high"),
            actual=_fmt_price(close),
            threshold=f">= {_fmt_price(max_below_high)}",
            explanation="Near yearly highs — institutional-quality stock.",
        ),
    ]
    passed = sum(1 for c in conditions if c.passed)
    return GateResult("Trend Template", passed, len(conditions), conditions)


def explain_vcp_setup(
    diag: dict[str, Any], settings: AppSettings
) -> GateResult:
    cfg = settings.setup_vcp
    range10 = _safe_float(diag, "range10")
    voldry = _safe_float(diag, "voldry")
    atr_sma10 = _safe_float(diag, "atr_pct_sma10")
    atr_sma20 = _safe_float(diag, "atr_pct_sma20")
    atr_pct = _safe_float(diag, "atr_pct")
    atr_sma30 = _safe_float(diag, "atr_pct_sma30")
    close = _safe_float(diag, "close")
    sma50 = _safe_float(diag, "sma50")
    extension_pct = (close / sma50 - 1.0) if sma50 else 0.0
    base_depth = _safe_float(diag, "base_depth")

    atr_ratio = (atr_sma10 / atr_sma30) if atr_sma30 else None

    conditions = [
        ConditionResult(
            name="Range tightened",
            passed=_safe_bool(diag, "range10_ok"),
            actual=_fmt_pct(range10),
            threshold=f"<= {_fmt_pct(cfg.max_range10_pct)}",
            explanation="Price range contracted — base is narrowing, sellers exhausting.",
        ),
        ConditionResult(
            name="Volume dried up",
            passed=_safe_bool(diag, "voldry_ok"),
            actual=_fmt_ratio(voldry),
            threshold=f"<= {_fmt_ratio(cfg.max_vol_dryup_ratio)}",
            explanation="Volume dry-up — fewer sellers remain, supply drying up.",
        ),
        ConditionResult(
            name="ATR declining",
            passed=_safe_bool(diag, "atr_trend_ok"),
            actual=f"10d {_fmt_pct(atr_sma10, 2)} vs 20d {_fmt_pct(atr_sma20, 2)}",
            threshold="10d < 20d",
            explanation="Volatility decreasing — calm before a potential move.",
        ),
        ConditionResult(
            name="ATR ratio (10d/30d) contracted",
            passed=_safe_bool(diag, "atr_contract_ok"),
            actual=f"{atr_ratio:.2f}" if atr_ratio is not None else "\u2014",
            threshold=f"<= {cfg.atr_contraction_factor:.2f}",
            explanation="Short-term volatility contracted relative to medium-term \u2014 compression before expansion.",
        ),
        ConditionResult(
            name="Not extended above SMA50",
            passed=_safe_bool(diag, "extension_ok"),
            actual=f"{extension_pct:+.1%}" if sma50 else "\u2014",
            threshold=f"<= +{cfg.max_extension_from_50sma_pct:.0%}",
            explanation="Not overextended — entry still has room to run.",
        ),
        ConditionResult(
            name="Base depth contained",
            passed=_safe_bool(diag, "base_depth_ok"),
            actual=_fmt_pct(base_depth),
            threshold=f"<= {_fmt_pct(cfg.base_max_depth_pct)}",
            explanation="Correction from peak is contained — healthy consolidation.",
        ),
    ]

    contraction_count = int(_safe_float(diag, "contraction_count"))
    if cfg.contraction_min_count > 0:
        conditions.append(ConditionResult(
            name="Contraction count",
            passed=contraction_count >= cfg.contraction_min_count,
            actual=str(contraction_count),
            threshold=f">= {cfg.contraction_min_count}",
            explanation="Successive narrowing contractions — the hallmark of a true VCP.",
        ))
    else:
        conditions.append(ConditionResult(
            name="Contraction count",
            passed=True,
            actual=str(contraction_count),
            threshold="informational",
            explanation="Successive narrowing contractions detected in the base (higher = better quality VCP).",
        ))

    passed = sum(1 for c in conditions if c.passed)
    return GateResult("VCP Setup", passed, len(conditions), conditions)


def explain_rs(
    signal_row: dict[str, Any], settings: AppSettings
) -> GateResult:
    rs_rank = _safe_float(signal_row, "rs_rank")
    rs_min = settings.relative_strength.rs_rank_min
    passed = rs_rank >= rs_min

    explanation = (
        f"Outperforming {rs_rank:.0f}% of the market — relative strength leader."
        if passed
        else f"Only outperforming {rs_rank:.0f}% of the market."
    )

    conditions = [
        ConditionResult(
            name="RS rank",
            passed=passed,
            actual=f"{rs_rank:.0f}",
            threshold=f">= {rs_min:.0f}",
            explanation=explanation,
        )
    ]
    return GateResult("Relative Strength", 1 if passed else 0, 1, conditions)


# ---------------------------------------------------------------------------
# Narrative builder
# ---------------------------------------------------------------------------

def build_narrative(
    signal_type: str | None,
    diag: dict[str, Any],
    signal_row: dict[str, Any],
    settings: AppSettings,
) -> str:
    symbol = signal_row.get("symbol", "\u2014")
    pivot = _safe_float(signal_row, "pivot")
    rs_rank = _safe_float(signal_row, "rs_rank")
    near_pivot = _safe_float(signal_row, "near_pivot")
    range10 = _safe_float(signal_row, "range10")
    voldry = _safe_float(signal_row, "voldry")
    vol_mult = _safe_float(signal_row, "vol_mult", 1.0)
    entry_ref = _safe_float(signal_row, "entry_ref")
    stop = _safe_float(signal_row, "stop")
    r_per_share = _safe_float(signal_row, "r_per_share")
    risk_pct = ((entry_ref - stop) / entry_ref * 100) if entry_ref else 0

    if signal_type == "BREAKOUT":
        return (
            f"{symbol} broke above its pivot ({_fmt_price(pivot)}) "
            f"on {vol_mult:.1f}x average volume \u2014 a volume-confirmed breakout "
            f"from a VCP base. "
            f"Entry ref: {_fmt_price(entry_ref)}. "
            f"Stop: {_fmt_price(stop)} ({risk_pct:.1f}% risk). "
            f"R/share: {_fmt_price(r_per_share)}."
        )

    if signal_type == "WATCH":
        return (
            f"{symbol} is in a confirmed Stage 2 uptrend (RS rank {rs_rank:.0f}) "
            f"building a tight VCP base \u2014 range contracted to {_fmt_pct(range10)} "
            f"while volume dried up to {_fmt_ratio(voldry)}. "
            f"The stock is {_fmt_pct(near_pivot)} below its pivot at {_fmt_price(pivot)}. "
            f"Worth monitoring for a breakout on volume."
        )

    return (
        f"{symbol} is in a Stage 2 uptrend (RS rank {rs_rank:.0f}) "
        f"with VCP setup conditions met. "
        f"Currently {_fmt_pct(near_pivot)} from pivot at {_fmt_price(pivot)}."
    )


# ---------------------------------------------------------------------------
# Risk flag detector
# ---------------------------------------------------------------------------

_BORDERLINE_PCT = 0.80


def detect_risk_flags(
    diag: dict[str, Any],
    signal_row: dict[str, Any],
    settings: AppSettings,
) -> list[RiskFlag]:
    flags: list[RiskFlag] = []

    rs_rank = _safe_float(signal_row, "rs_rank")
    vol_mult = _safe_float(signal_row, "vol_mult", 1.0)
    close = _safe_float(diag, "close")
    sma50 = _safe_float(diag, "sma50")
    adv50 = _safe_float(signal_row, "adv50")
    range10 = _safe_float(signal_row, "range10")
    voldry = _safe_float(signal_row, "voldry")
    entry_ref = _safe_float(signal_row, "entry_ref")
    stop = _safe_float(signal_row, "stop")
    signal_type = signal_row.get("signal_type")

    rs_min = settings.relative_strength.rs_rank_min
    adv_min = settings.universe.min_adv50_inr
    max_ext = settings.setup_vcp.max_extension_from_50sma_pct
    max_range = settings.setup_vcp.max_range10_pct
    max_voldry = settings.setup_vcp.max_vol_dryup_ratio

    if rs_min <= rs_rank < rs_min + 10:
        flags.append(RiskFlag(
            f"RS rank ({rs_rank:.0f}) is near the minimum threshold "
            f"({rs_min:.0f}) \u2014 relatively weak among qualifiers."
        ))

    if signal_type == "BREAKOUT" and 1.5 <= vol_mult < 2.0:
        flags.append(RiskFlag(
            f"Volume multiplier ({vol_mult:.1f}x) is just above minimum "
            f"(1.5x) \u2014 confirmation is thin."
        ))

    if sma50 and close:
        ext_pct = (close / sma50 - 1) * 100
        limit_pct = max_ext * 100
        if ext_pct > limit_pct * _BORDERLINE_PCT:
            flags.append(RiskFlag(
                f"Close is {ext_pct:.1f}% above SMA50 \u2014 "
                f"approaching extension limit ({limit_pct:.0f}%)."
            ))

    if adv50 and adv50 < adv_min * 1.4:
        adv_cr = adv50 / 1e7
        min_cr = adv_min / 1e7
        flags.append(RiskFlag(
            f"ADV50 is \u20b9{adv_cr:.0f}Cr \u2014 near the minimum "
            f"liquidity threshold (\u20b9{min_cr:.0f}Cr)."
        ))

    if range10 and range10 > max_range * _BORDERLINE_PCT:
        flags.append(RiskFlag(
            f"Range ({_fmt_pct(range10)}) approaching maximum "
            f"threshold ({_fmt_pct(max_range)})."
        ))

    if voldry and voldry > max_voldry * 0.88:
        flags.append(RiskFlag(
            f"Volume dry-up ({_fmt_ratio(voldry)}) near maximum "
            f"threshold ({_fmt_ratio(max_voldry)})."
        ))

    if entry_ref and stop:
        risk_pct = ((entry_ref - stop) / entry_ref) * 100
        if risk_pct > 6:
            flags.append(RiskFlag(
                f"Stop distance is {risk_pct:.1f}% \u2014 wider than typical."
            ))

    contraction_count = int(_safe_float(diag, "contraction_count"))
    if contraction_count < 2:
        flags.append(RiskFlag(
            f"Contraction count ({contraction_count}) is low \u2014 "
            f"may not be a proper VCP with successive tightenings."
        ))

    return flags


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------

def build_explanation(
    diag: dict[str, Any],
    signal_row: dict[str, Any],
    settings: AppSettings,
) -> SignalExplanation:
    """Build a complete explanation for a signal or watchlist candidate.

    ``diag`` comes from ``compute_symbol_diagnostics``.
    ``signal_row`` comes from the merged setup row (signals / watchlist / reviews).
    """
    signal_type = signal_row.get("signal_type")

    trend_gate = explain_trend_template(diag, settings)
    vcp_gate = explain_vcp_setup(diag, settings)
    rs_gate = explain_rs(signal_row, settings)

    narrative = build_narrative(signal_type, diag, signal_row, settings)
    risk_flags = detect_risk_flags(diag, signal_row, settings)

    return SignalExplanation(
        narrative=narrative,
        gates=[trend_gate, vcp_gate, rs_gate],
        risk_flags=risk_flags,
    )
