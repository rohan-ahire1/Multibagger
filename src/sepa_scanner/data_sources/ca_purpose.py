"""Parse NSE corporate-action purpose strings into price multipliers.

The fetched CA feed leaves 23,974 rows as ``action_type="unparsed"``, keeping
only the free-text purpose. Several hundred of those are face-value splits and
bonuses -- real capital actions that the adjustment pipeline therefore never
applies, leaving a cliff in the panel while the feed appears to know about the
event.

The strings are terse and inconsistent but not ambiguous::

    Fv Spl-Rs10tors2/Bon-12:1
    Fv Split Rs.10/- To Re.1/
    Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share
    Bonus 1:1

Only face-value splits, consolidations and bonuses are parsed. Anything else --
demergers, schemes of arrangement, buybacks -- returns ``None`` so it goes to
manual review rather than being guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# "Rs10tors2", "Rs.10/- To Re.1/", "From Rs 10/- Per Share To Re 1/- Per Share"
_FV_PATTERN = re.compile(
    r"(?:rs|re)\.?\s*(\d+(?:\.\d+)?)\s*(?:/-)?\s*(?:per\s+share\s*)?to\s*(?:rs|re)\.?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
# "Bon-12:1", "Bonus 1:1", "Bonus Issue 3:2"
_BONUS_PATTERN = re.compile(r"bon(?:us)?(?:\s*issue)?[\s\-:]*(\d+)\s*:\s*(\d+)", re.IGNORECASE)

_SPLIT_HINTS = ("split", "splt", "sub-divi", "subdivi", "face value", "fv ", "consolidat")


@dataclass(frozen=True)
class ParsedAction:
    """A capital action recovered from free text."""

    multiplier: float
    """Price multiplier applied to bars before the ex-date."""
    kind: str
    """One of ``split``, ``bonus``, ``bonus_split``."""
    detail: str
    """Human-readable summary, for the override file's description column."""


def _face_value_multiplier(text: str) -> tuple[float, str] | None:
    match = _FV_PATTERN.search(text)
    if match is None:
        return None
    old, new = float(match.group(1)), float(match.group(2))
    if old <= 0 or new <= 0 or old == new:
        return None
    # Price scales with face value: Rs10 -> Re1 is a tenfold split.
    return new / old, f"FV {old:g}->{new:g}"


def _bonus_multiplier(text: str) -> tuple[float, str] | None:
    match = _BONUS_PATTERN.search(text)
    if match is None:
        return None
    new, held = float(match.group(1)), float(match.group(2))
    if new <= 0 or held <= 0:
        return None
    # `new` extra shares for every `held` held: price scales by held/(new+held).
    return held / (new + held), f"bonus {match.group(1)}:{match.group(2)}"


def parse_purpose(purpose: str | None) -> ParsedAction | None:
    """Recover a price multiplier from a purpose string, or ``None``.

    A string carrying both legs -- ``Fv Spl-Rs10tors2/Bon-12:1`` -- multiplies
    them, so no residual cliff is left behind.
    """
    if not purpose:
        return None
    text = purpose.strip()
    lowered = text.lower()

    fv = _face_value_multiplier(text) if any(h in lowered for h in _SPLIT_HINTS) else None
    bonus = _bonus_multiplier(text)

    if fv is None and bonus is None:
        return None
    if fv is not None and bonus is not None:
        return ParsedAction(fv[0] * bonus[0], "bonus_split", f"{fv[1]} + {bonus[1]}")
    if fv is not None:
        return ParsedAction(fv[0], "split", fv[1])
    assert bonus is not None
    return ParsedAction(bonus[0], "bonus", bonus[1])
