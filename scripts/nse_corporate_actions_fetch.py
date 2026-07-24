"""Fetch NSE equity corporate actions (split / bonus / dividend) and parse to factors.

Source: NSE corporate-actions API (free). Paged by yearly date windows across history.
Output: ``data/reference/nse_corporate_actions.parquet`` -- the canonical CA source
consumed by ``sepa_scanner.data_sources.adjustment.apply_adjustment``.

Free-text ``subject`` strings are parsed into structured events; anything unparseable is
retained with ``action_type='unparsed'`` and its raw text for manual review, never silently
dropped.

Usage:
    uv run python scripts/nse_corporate_actions_fetch.py --start 2005 --end 2026
"""
from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import date, datetime
from pathlib import Path

import polars as pl

from sepa_scanner.utils.http import build_nse_session
from sepa_scanner.utils.io import ensure_directory

LOGGER = logging.getLogger("nse_ca_fetch")

CA_URL = "https://www.nseindia.com/api/corporates-corporateActions"
OUT_PATH = Path("data/reference/nse_corporate_actions.parquet")

_FV_SPLIT = re.compile(
    r"split.*?from\s*rs?\.?\s*([\d.]+).*?to\s*rs?\.?\s*([\d.]+)", re.IGNORECASE
)
_BONUS = re.compile(r"bonus\s*(\d+)\s*:\s*(\d+)", re.IGNORECASE)
_DIV = re.compile(r"dividend.*?rs?\.?\s*([\d.]+)", re.IGNORECASE)


def parse_subject(subject: str) -> dict:
    """Parse an NSE CA ``subject`` string into a structured event dict.

    Handles COMBINED subjects where a bonus and a face-value split co-occur, e.g.
    "Bonus 1:1 / Face Value Split From Rs.10/- To Re.1/-" -> both legs captured, so the
    downstream multiplier is bonus_mult * fv_mult. First-match-wins previously dropped a
    leg and left a residual price cliff.
    """
    s = subject or ""
    out = {
        "action_type": "unparsed",
        "ratio_num": None,
        "ratio_den": None,
        "old_face_value": None,
        "new_face_value": None,
        "dividend_amount": None,
        "raw_purpose": s,
    }
    legs: list[str] = []
    fv = _FV_SPLIT.search(s)
    if fv:
        out["old_face_value"] = float(fv.group(1))
        out["new_face_value"] = float(fv.group(2))
        legs.append("split")
    bon = _BONUS.search(s)
    if bon:
        out["ratio_num"] = int(bon.group(1))
        out["ratio_den"] = int(bon.group(2))
        legs.append("bonus")
    if legs:
        # "bonus_split" when both legs present; else the single leg.
        out["action_type"] = "bonus_split" if len(legs) == 2 else legs[0]
        return out
    div = _DIV.search(s)
    if div and "dividend" in s.lower():
        out.update(action_type="dividend", dividend_amount=float(div.group(1)))
        return out
    return out


def _parse_ex_date(raw: str) -> date | None:
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def fetch_window(session, from_d: date, to_d: date) -> list[dict]:
    params = {
        "index": "equities",
        "from_date": from_d.strftime("%d-%m-%Y"),
        "to_date": to_d.strftime("%d-%m-%Y"),
    }
    resp = session.get(CA_URL, params=params, timeout=30,
                       headers={"Accept": "application/json"})
    resp.raise_for_status()
    payload = resp.json()
    rows: list[dict] = []
    for item in payload if isinstance(payload, list) else []:
        subject = item.get("subject") or item.get("purpose") or ""
        ex = _parse_ex_date(item.get("exDate") or item.get("ex_date") or "")
        if ex is None:
            continue
        parsed = parse_subject(subject)
        rows.append({
            "symbol": (item.get("symbol") or "").strip(),
            "isin": (item.get("isin") or "").strip() or None,
            "ex_date": ex,
            **parsed,
            "source": "nse",
        })
    return rows


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=int, default=2005)
    ap.add_argument("--end", type=int, default=date.today().year)
    ap.add_argument("--sleep", type=float, default=0.6)
    args = ap.parse_args()

    session = build_nse_session()
    try:
        session.get("https://www.nseindia.com/", timeout=15)
    except Exception:  # noqa: BLE001
        LOGGER.debug("bootstrap failed; continuing")

    all_rows: list[dict] = []
    for year in range(args.start, args.end + 1):
        # NSE caps the window; fetch in halves to be safe.
        for a, b in [(date(year, 1, 1), date(year, 6, 30)),
                     (date(year, 7, 1), date(year, 12, 31))]:
            try:
                rows = fetch_window(session, a, b)
                all_rows.extend(rows)
                LOGGER.info("%s..%s: %d events", a, b, len(rows))
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("window %s..%s failed: %s", a, b, exc)
            time.sleep(args.sleep)

    ensure_directory(OUT_PATH.parent)
    schema = {
        "symbol": pl.Utf8, "isin": pl.Utf8, "ex_date": pl.Date, "action_type": pl.Utf8,
        "ratio_num": pl.Int64, "ratio_den": pl.Int64, "old_face_value": pl.Float64,
        "new_face_value": pl.Float64, "dividend_amount": pl.Float64,
        "raw_purpose": pl.Utf8, "source": pl.Utf8,
    }
    df = pl.DataFrame(all_rows, schema=schema).unique(
        subset=["symbol", "ex_date", "action_type"], keep="first"
    ).sort(["symbol", "ex_date"])
    df.write_parquet(OUT_PATH)
    counts = df.group_by("action_type").len().sort("len", descending=True)
    LOGGER.info("Wrote %d CA events to %s\n%s", df.height, OUT_PATH, counts)


if __name__ == "__main__":
    main()
