"""Realistic NSE equity-delivery transaction friction for the live paper portfolio.

Mirrors the validated SEPA backtest model in
``src/sepa_scanner/backtest/simulation.py`` so paper trades pay the same
costs a real Dhan delivery order would incur. Discount-broker (Dhan)
delivery assumptions, FY2026:

  - Brokerage:        Rs 0 (Dhan delivery is free)
  - STT:              0.10% on buy + 0.10% on sell (turnover)
  - Stamp duty:       0.015% on buy only
  - Exchange (NSE):   0.0030699% each side
  - SEBI:             0.0001% each side
  - GST:              18% on (exchange + SEBI + brokerage)
  - DP charge:        Rs 12.50 + 18% GST = Rs 14.75 per sell (per scrip)
  - Slippage:         0.05% adverse fill each side

Net multiplicative factors (statutory charges only, excluding slippage),
verified against Dhan's published delivery rates to ~0.0001% of turnover:
  buy  -> +0.118658%  (BUY_TAX_MULT  = 1.00118658)
  sell -> -0.103658%  (SELL_TAX_MULT = 0.99896342)

Slippage is an execution assumption, not a Dhan fee. 5 bps each side is a
realistic mid-estimate for liquid Nifty 100 large-caps filling at the open;
it is the dominant cost in the model, so tune SLIPPAGE_*_MULT if your fills
differ. The statutory factors match the SEPA engine's ``tax_buy`` /
``tax_sell`` constants, keeping results comparable across engines.
"""

from __future__ import annotations

SLIPPAGE_BUY_MULT = 1.0005
SLIPPAGE_SELL_MULT = 0.9995
BUY_TAX_MULT = 1.00118658
SELL_TAX_MULT = 0.99896342
DP_CHARGE_PER_SELL = 14.75


def buy_fill_price(raw_price: float) -> float:
    """Per-share price actually paid after adverse slippage (excl. charges)."""
    return raw_price * SLIPPAGE_BUY_MULT


def sell_fill_price(raw_price: float) -> float:
    """Per-share price actually received after adverse slippage (excl. charges)."""
    return raw_price * SLIPPAGE_SELL_MULT


def buy_cash_outflow(shares: float, raw_price: float) -> tuple[float, float, float]:
    """Cash required to buy ``shares`` at ``raw_price``.

    Returns ``(fill_price, charges, total_cash_out)`` where ``total_cash_out``
    is ``shares * fill_price`` plus all statutory buy-side charges.
    """
    fill = buy_fill_price(raw_price)
    gross = shares * fill
    charges = gross * (BUY_TAX_MULT - 1.0)
    return fill, charges, gross + charges


def sell_cash_inflow(shares: float, raw_price: float) -> tuple[float, float, float, float]:
    """Cash received from selling ``shares`` at ``raw_price``.

    Returns ``(fill_price, charges, dp_charge, net_cash_in)`` where
    ``net_cash_in`` is ``shares * fill_price`` minus statutory sell-side
    charges and the flat DP charge.
    """
    fill = sell_fill_price(raw_price)
    gross = shares * fill
    charges = gross * (1.0 - SELL_TAX_MULT)
    net = gross - charges - DP_CHARGE_PER_SELL
    return fill, charges, DP_CHARGE_PER_SELL, net
