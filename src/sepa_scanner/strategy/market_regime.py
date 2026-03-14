from __future__ import annotations

from enum import Enum


class MarketRegime(str, Enum):
    STRONG = "strong"     # >60% breadth: full position sizes, max positions
    NEUTRAL = "neutral"   # 40-60% breadth: half sizes, A+ setups only
    WEAK = "weak"         # <40% breadth: no new entries
