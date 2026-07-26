"""Indian equity-delivery transaction costs for portfolio order simulation."""

from __future__ import annotations

from typing import Literal


def indian_delivery_cost(
    side: Literal["buy", "sell"], notional: float, *, brokerage_rate: float = 0.0,
    stress_multiplier: float = 1.0,
) -> float:
    """Configured delivery charges, optionally scaled for the specified stress runs."""
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if notional < 0 or stress_multiplier < 0:
        raise ValueError("notional and stress_multiplier must be non-negative")
    brokerage = notional * brokerage_rate
    stt = notional * 0.001
    exchange = notional * 0.0000297
    sebi = notional * (10.0 / 10_000_000.0)
    gst = 0.18 * (brokerage + exchange + sebi)
    stamp = notional * 0.00015 if side == "buy" else 0.0
    return (brokerage + stt + exchange + sebi + gst + stamp) * stress_multiplier
