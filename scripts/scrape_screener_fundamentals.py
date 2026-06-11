"""Scrape quarterly fundamentals (Sales, EPS) from screener.in.

Prototype data source for the Minervini fundamental gate. Fetches each
company's quarterly results table, parses Sales + EPS per quarter, computes
YoY EPS growth, and writes a CSV in the format
``sepa_scanner.data_sources.fundamentals.load_quarterly_results`` expects:
``symbol, quarter_end, eps, revenue, yoy_eps_growth``.

screener.in's public quarterly table holds ~13 quarters (~3 years), so YoY
growth is available for roughly the last ~9 quarters per name. This is a
hypothesis-test source only (recent, current-constituents = survivorship
bias); if the gate shows lift, move to a survivorship-clean history source.

Usage:
  .venv/bin/python scripts/scrape_screener_fundamentals.py [--limit N] [--symbols A,B]
  --force   re-fetch even if cached HTML exists
"""
from __future__ import annotations

import argparse
import calendar
import re
import sys
import time
from datetime import date
from pathlib import Path

import duckdb
import polars as pl
import requests
from bs4 import BeautifulSoup

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

CACHE_DIR = _ROOT / "data" / "raw" / "screener"
OUT_CSV = _ROOT / "data" / "reference" / "screener_quarterly.csv"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120 Safari/537.36"
    )
}
_MONTHS = {m: i for i, m in enumerate(calendar.month_abbr) if m}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scrape screener.in quarterly fundamentals.")
    p.add_argument("--limit", type=int, default=None, help="Only scrape first N symbols (testing)")
    p.add_argument("--symbols", type=str, default=None, help="Comma-separated symbols (default: all breakout signal symbols)")
    p.add_argument("--force", action="store_true", help="Re-fetch even if cached HTML exists")
    p.add_argument("--sleep", type=float, default=1.5, help="Seconds between requests (be polite)")
    return p.parse_args()


def signal_symbols() -> list[str]:
    conn = duckdb.connect(str(_ROOT / "data" / "db" / "sepa.duckdb"), read_only=True)
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM signals WHERE signal_type='BREAKOUT' ORDER BY symbol"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def quarter_end(label: str) -> date | None:
    """'Mar 2023' -> 2023-03-31 (last day of that month)."""
    m = re.match(r"([A-Za-z]{3})\s+(\d{4})", label.strip())
    if not m:
        return None
    mon = _MONTHS.get(m.group(1).capitalize())
    if not mon:
        return None
    yr = int(m.group(2))
    return date(yr, mon, calendar.monthrange(yr, mon)[1])


def _num(text: str) -> float | None:
    t = text.strip().replace(",", "").replace("%", "")
    if t in ("", "-", "—"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def fetch_html(symbol: str, *, force: bool, sleep: float) -> str | None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{symbol}.html"
    if cache.exists() and not force:
        return cache.read_text(encoding="utf-8")
    # consolidated preferred; fall back to standalone
    for suffix in ("consolidated/", ""):
        url = f"https://www.screener.in/company/{symbol}/{suffix}"
        for attempt in range(3):
            try:
                r = requests.get(url, headers=HEADERS, timeout=25)
                if r.status_code == 200 and 'id="quarters"' in r.text:
                    cache.write_text(r.text, encoding="utf-8")
                    time.sleep(sleep)
                    return r.text
                if r.status_code == 404:
                    break  # try next suffix
                if r.status_code in (429, 503):
                    time.sleep(sleep * (attempt + 2) * 2)
            except requests.RequestException:
                time.sleep(sleep * (attempt + 1))
        time.sleep(sleep)
    return None


def parse_quarters(html: str, symbol: str) -> list[dict]:
    """Extract per-quarter Sales + EPS rows from the #quarters table."""
    soup = BeautifulSoup(html, "html.parser")
    section = soup.find(id="quarters")
    if section is None:
        return []
    table = section.find("table")
    if table is None:
        return []
    head_cells = table.find("thead").find_all("th")
    quarters = [quarter_end(c.get_text()) for c in head_cells[1:]]  # first th is blank/row-label

    sales_row: list[float | None] = []
    eps_row: list[float | None] = []
    for tr in table.find("tbody").find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        label = cells[0].get_text(strip=True).lower()
        vals = [_num(c.get_text()) for c in cells[1:]]
        if label.startswith("sales") or label.startswith("revenue"):
            sales_row = vals
        elif label.startswith("eps"):
            eps_row = vals

    rows = []
    for i, q in enumerate(quarters):
        if q is None:
            continue
        rows.append({
            "symbol": symbol,
            "quarter_end": q,
            "eps": eps_row[i] if i < len(eps_row) else None,
            "revenue": sales_row[i] if i < len(sales_row) else None,
        })
    return rows


def add_yoy_growth(df: pl.DataFrame) -> pl.DataFrame:
    """YoY EPS growth = eps[q] / eps[q-4] - 1, per symbol (quarters sorted)."""
    df = df.sort(["symbol", "quarter_end"])
    return df.with_columns(
        pl.col("eps").shift(4).over("symbol").alias("_eps_yoy_ago")
    ).with_columns(
        pl.when((pl.col("_eps_yoy_ago").is_not_null()) & (pl.col("_eps_yoy_ago").abs() > 1e-9))
        .then(pl.col("eps") / pl.col("_eps_yoy_ago") - 1.0)
        .otherwise(None)
        .alias("yoy_eps_growth")
    ).drop("_eps_yoy_ago")


def main() -> None:
    args = parse_args()
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",")]
        if args.symbols else signal_symbols()
    )
    if args.limit:
        symbols = symbols[: args.limit]

    print(f"Scraping {len(symbols)} symbols from screener.in (sleep={args.sleep}s)...")
    all_rows: list[dict] = []
    ok = fail = 0
    for i, sym in enumerate(symbols, 1):
        html = fetch_html(sym, force=args.force, sleep=args.sleep)
        if html is None:
            fail += 1
            print(f"  [{i}/{len(symbols)}] {sym}: NO DATA")
            continue
        rows = parse_quarters(html, sym)
        if rows:
            all_rows.extend(rows)
            ok += 1
            if i % 25 == 0 or i <= 5:
                print(f"  [{i}/{len(symbols)}] {sym}: {len(rows)} quarters")
        else:
            fail += 1
            print(f"  [{i}/{len(symbols)}] {sym}: parse failed")

    if not all_rows:
        print("No data scraped.")
        return
    df = add_yoy_growth(pl.DataFrame(all_rows))
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(OUT_CSV)
    cov = df.filter(pl.col("yoy_eps_growth").is_not_null()).height
    print(f"\nDone. {ok} symbols ok, {fail} failed.")
    print(f"Wrote {df.height} quarter-rows ({cov} with YoY growth) to {OUT_CSV}")


if __name__ == "__main__":
    main()
