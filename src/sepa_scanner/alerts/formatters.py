from __future__ import annotations

from typing import Any

from sepa_scanner.settings import AppSettings


def format_watch_message(row: dict[str, Any]) -> str:
    return (
        f"[WATCH] NSE:{row['symbol']}\n"
        f"Close: Rs{row['close']:.2f} | Pivot(40D): Rs{row['pivot']:.2f} | NearPivot: {row['near_pivot']:.1%}\n"
        f"RSrank: {row['rs_rank']:.0f} | Range10: {row['range10']:.1%} | VolDryUp(10/50): {row['voldry']:.2f}\n"
        f"ATR%: {row['atr_pct']:.2%} | ADV50: Rs{row['adv50'] / 1e7:.1f} Cr | Vol50: {row['vol50']:.0f}"
    )


def format_breakout_message(
    row: dict[str, Any], settings: AppSettings, *, market_filter_on: bool
) -> str:
    return (
        f"[BREAKOUT] NSE:{row['symbol']}\n"
        f"Close: Rs{row['close']:.2f} | Pivot: Rs{row['pivot']:.2f} | "
        f"Vol: {row['vol_mult']:.2f}x (>= {settings.breakout.breakout_vol_mult:.1f}x)\n"
        f"EntryRef: Rs{row['entry_ref']:.2f} | Stop: Rs{row['stop']:.2f} | "
        f"Risk/share (R): Rs{row['r_per_share']:.2f}\n"
        f"RSrank: {row['rs_rank']:.0f} | Range10: {row['range10']:.1%} | ATR%: {row['atr_pct']:.2%}\n"
        f"MarketFilter: {market_filter_on}"
    )
