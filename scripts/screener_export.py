"""Fetch screener.in's logged-in 'Export to Excel' (deeper history than the web table).

The public company page shows ~13 quarters; the authenticated Excel export
(POST /user/company/export/{warehouse_id}/) returns ~10-12 years of annual data
plus the quarterly section. Requires YOUR logged-in screener session.

Setup (one time):
  1. Log in to https://www.screener.in in a browser.
  2. Open devtools > Application/Storage > Cookies > screener.in.
  3. Copy the values of `sessionid` and `csrftoken`.
  4. Write them to data/tmp/screener_cookies.txt as a single line:
        sessionid=<value>; csrftoken=<value>
     (this file is gitignored)

Usage:
  .venv/bin/python scripts/screener_export.py --probe RELIANCE   # fetch+inspect one
  .venv/bin/python scripts/screener_export.py [--limit N]        # batch all signal symbols
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import duckdb
import requests

_ROOT = Path(__file__).resolve().parents[1]
COOKIE_FILE = _ROOT / "data" / "tmp" / "screener_cookies.txt"
HTML_CACHE = _ROOT / "data" / "raw" / "screener"
XLSX_DIR = _ROOT / "data" / "raw" / "screener_xlsx"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")


def load_cookies() -> dict[str, str]:
    if not COOKIE_FILE.exists():
        sys.exit(f"Missing {COOKIE_FILE}. See the docstring: write 'sessionid=..; csrftoken=..'")
    raw = COOKIE_FILE.read_text().strip()
    jar = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            jar[k.strip()] = v.strip()
    if "sessionid" not in jar:
        sys.exit("Cookie file must contain sessionid (and ideally csrftoken).")
    return jar


def signal_symbols() -> list[str]:
    conn = duckdb.connect(str(_ROOT / "data" / "db" / "sepa.duckdb"), read_only=True)
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM signals WHERE signal_type='BREAKOUT' ORDER BY symbol"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def fetch_page(session: requests.Session, symbol: str) -> str | None:
    for suffix in ("consolidated/", ""):
        url = f"https://www.screener.in/company/{symbol}/{suffix}"
        try:
            r = session.get(url, timeout=25)
            if r.status_code == 200 and "warehouse-id" in r.text:
                return r.text
        except requests.RequestException:
            pass
    return None


def export_xlsx(session: requests.Session, symbol: str, sleep: float) -> Path | None:
    XLSX_DIR.mkdir(parents=True, exist_ok=True)
    out = XLSX_DIR / f"{symbol}.xlsx"
    html = fetch_page(session, symbol)
    if html is None:
        return None
    m_wh = re.search(r'warehouse-id="(\d+)"', html)
    m_csrf = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', html)
    if not m_wh:
        return None
    wh = m_wh.group(1)
    url = f"https://www.screener.in/user/company/export/{wh}/"
    data = {}
    if m_csrf:
        data["csrfmiddlewaretoken"] = m_csrf.group(1)
    headers = {"Referer": f"https://www.screener.in/company/{symbol}/"}
    try:
        r = session.post(url, data=data, headers=headers, timeout=30)
    except requests.RequestException:
        return None
    ct = r.headers.get("Content-Type", "")
    if r.status_code == 200 and ("spreadsheet" in ct or "excel" in ct or r.content[:2] == b"PK"):
        out.write_bytes(r.content)
        time.sleep(sleep)
        return out
    return None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--probe", type=str, default=None, help="Fetch one symbol and stop")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--sleep", type=float, default=2.0)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    jar = load_cookies()
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    session.cookies.update(jar)

    symbols = [args.probe] if args.probe else signal_symbols()
    if args.limit:
        symbols = symbols[: args.limit]

    ok = fail = 0
    for i, sym in enumerate(symbols, 1):
        if not args.force and (XLSX_DIR / f"{sym}.xlsx").exists():
            ok += 1
            continue
        res = export_xlsx(session, sym, args.sleep)
        if res:
            ok += 1
            if args.probe or i <= 3 or i % 25 == 0:
                print(f"  [{i}/{len(symbols)}] {sym}: saved {res.name} ({res.stat().st_size} bytes)")
        else:
            fail += 1
            print(f"  [{i}/{len(symbols)}] {sym}: FAILED (auth? no warehouse-id?)")
        if args.probe:
            break
    print(f"\nDone. {ok} ok, {fail} failed. xlsx in {XLSX_DIR}")


if __name__ == "__main__":
    main()
