from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import yfinance as yf


ETF_SYMBOLS = {
    "AUTOBEES",
    "AXISGOLD",
    "BANKBEES",
    "BSLGOLDETF",
    "CPSEETF",
    "GOLDBEES",
    "HNGSNGBEES",
    "ICICIB22",
    "ITBEES",
    "JUNIORBEES",
    "KOTAKGOLD",
    "GOLD1",
    "GOLDBETA",
    "HDFCSILVER",
    "LICNETFGSC",
    "LICNETFN50",
    "LIQUIDCASE",
    "MAFANG",
    "MID150BEES",
    "MON100",
    "NIFTYBEES",
    "PSUBNKBEES",
    "SETFNIF50",
    "SETFGOLD",
    "SILVER",
    "SILVERADD",
}


LEGACY_SECTOR_MAP = {
    "AEGISCHEM": "Oil Gas & Consumable Fuels",
    "AHMEDFORGE": "Capital Goods",
    "AMARAJABAT": "Automobile and Auto Components",
    "CAIRN": "Oil Gas & Consumable Fuels",
    "CAPF": "Financial Services",
    "CENTURYTEX": "Forest Materials",
    "COSMOFILMS": "Consumer Durables",
    "ERAINFRA": "Construction",
    "GDL": "Services",
    "GMRINFRA": "Construction",
    "GUJRATGAS": "Oil Gas & Consumable Fuels",
    "HEXAWARE": "Information Technology",
    "IBULHSGFIN": "Financial Services",
    "INOXLEISUR": "Media Entertainment & Publication",
    "JSLHISAR": "Metals & Mining",
    "JUBILANT": "Chemicals",
    "KPIT": "Information Technology",
    "MANGCHEFER": "Chemicals",
    "MAX": "Healthcare",
    "MCDOWELL-N": "Fast Moving Consumer Goods",
    "MEGH": "Chemicals",
    "MINDAIND": "Automobile and Auto Components",
    "MINDTREE": "Information Technology",
    "NIITTECH": "Information Technology",
    "OPTOCIRCUI": "Healthcare",
    "ORIENTREF": "Capital Goods",
    "PHILIPCARB": "Chemicals",
    "PVR": "Media Entertainment & Publication",
    "RELCAPITAL": "Financial Services",
    "SANGHIIND": "Construction Materials",
    "SATYAMCOMP": "Information Technology",
    "SINTEX": "Consumer Durables",
    "SKSMICRO": "Financial Services",
    "STRTECH": "Telecommunication",
    "TATACOFFEE": "Fast Moving Consumer Goods",
    "TATAGLOBAL": "Fast Moving Consumer Goods",
    "TATAMETALI": "Metals & Mining",
    "TULIP": "Information Technology",
    "VIDEOIND": "Consumer Durables",
    "VIJAYABANK": "Financial Services",
    "WELSPUNIND": "Textiles",
}


YAHOO_SECTOR_MAP = {
    "Basic Materials": "Metals & Mining",
    "Communication Services": "Telecommunication",
    "Consumer Cyclical": "Consumer Durables",
    "Consumer Defensive": "Fast Moving Consumer Goods",
    "Energy": "Oil Gas & Consumable Fuels",
    "Financial Services": "Financial Services",
    "Healthcare": "Healthcare",
    "Industrials": "Capital Goods",
    "Real Estate": "Realty",
    "Technology": "Information Technology",
    "Utilities": "Power",
}


INDUSTRY_KEYWORDS = [
    ("bank", "Financial Services"),
    ("insurance", "Financial Services"),
    ("asset management", "Financial Services"),
    ("credit", "Financial Services"),
    ("mortgage", "Financial Services"),
    ("auto", "Automobile and Auto Components"),
    ("vehicle", "Automobile and Auto Components"),
    ("steel", "Metals & Mining"),
    ("aluminum", "Metals & Mining"),
    ("copper", "Metals & Mining"),
    ("mining", "Metals & Mining"),
    ("cement", "Construction Materials"),
    ("construction", "Construction"),
    ("engineering", "Capital Goods"),
    ("machinery", "Capital Goods"),
    ("electrical", "Capital Goods"),
    ("chemical", "Chemicals"),
    ("fertilizer", "Chemicals"),
    ("pharma", "Healthcare"),
    ("drug", "Healthcare"),
    ("hospital", "Healthcare"),
    ("biotech", "Healthcare"),
    ("software", "Information Technology"),
    ("information technology", "Information Technology"),
    ("semiconductor", "Information Technology"),
    ("telecom", "Telecommunication"),
    ("media", "Media Entertainment & Publication"),
    ("entertainment", "Media Entertainment & Publication"),
    ("hotel", "Consumer Services"),
    ("restaurant", "Consumer Services"),
    ("retail", "Consumer Services"),
    ("textile", "Textiles"),
    ("apparel", "Textiles"),
    ("oil", "Oil Gas & Consumable Fuels"),
    ("gas", "Oil Gas & Consumable Fuels"),
    ("coal", "Oil Gas & Consumable Fuels"),
    ("power", "Power"),
    ("utility", "Power"),
    ("real estate", "Realty"),
    ("logistics", "Services"),
    ("transport", "Services"),
    ("paper", "Forest Materials"),
    ("packaging", "Consumer Durables"),
    ("food", "Fast Moving Consumer Goods"),
    ("beverage", "Fast Moving Consumer Goods"),
    ("tobacco", "Fast Moving Consumer Goods"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a comprehensive symbol sector map.")
    parser.add_argument("--db", type=Path, default=Path("data/db/sepa.duckdb"))
    parser.add_argument("--reference-dir", type=Path, default=Path("data/reference"))
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().upper().removesuffix(".NS").removesuffix(".BO")


def signal_symbols(db_path: Path) -> list[str]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT DISTINCT symbol
            FROM signals
            WHERE signal_type = 'BREAKOUT'
            ORDER BY symbol
            """
        ).fetchall()
    finally:
        con.close()
    return [normalize_symbol(row[0]) for row in rows]


def load_nifty500(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    df = pl.read_csv(path)
    return {
        normalize_symbol(row["Symbol"]): str(row["Industry"]).strip()
        for row in df.select("Symbol", "Industry").to_dicts()
        if row.get("Symbol") and row.get("Industry")
    }


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def is_etf(symbol: str) -> bool:
    return symbol in ETF_SYMBOLS or symbol.endswith(("BEES", "ETF", "GOLD"))


def map_yahoo_sector(raw_sector: str | None, raw_industry: str | None) -> str:
    sector = (raw_sector or "").strip()
    industry = (raw_industry or "").strip()
    haystack = f"{sector} {industry}".lower()
    for keyword, mapped in INDUSTRY_KEYWORDS:
        if keyword in haystack:
            return mapped
    return YAHOO_SECTOR_MAP.get(sector, "UNKNOWN")


def fetch_yahoo_info(symbol: str) -> dict[str, Any]:
    info = yf.Ticker(f"{symbol}.NS").info
    raw_sector = info.get("sector") or "UNKNOWN"
    raw_industry = info.get("industry") or "UNKNOWN"
    return {
        "raw_sector": raw_sector,
        "raw_industry": raw_industry,
        "industry": map_yahoo_sector(raw_sector, raw_industry),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
    }


def main() -> None:
    args = parse_args()
    args.reference_dir.mkdir(parents=True, exist_ok=True)
    base_path = args.reference_dir / "nifty500_constituents.csv"
    cache_path = args.reference_dir / "yfinance_sector_cache.json"
    output_path = args.reference_dir / "full_sector_map.csv"

    symbols = signal_symbols(args.db)
    base = load_nifty500(base_path)
    cache = load_cache(cache_path)

    rows: list[dict[str, str]] = []
    uncovered = [symbol for symbol in symbols if symbol not in base]
    if args.limit is not None:
        uncovered = uncovered[: args.limit]

    for symbol in symbols:
        if symbol in base:
            rows.append({"Symbol": symbol, "Industry": base[symbol], "Source": "nifty500"})

    fetched = 0
    for symbol in uncovered:
        if is_etf(symbol):
            cache[symbol] = {
                "raw_sector": "ETF",
                "raw_industry": "ETF",
                "industry": "ETF",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "status": "manual_etf",
            }
        elif symbol in LEGACY_SECTOR_MAP and (
            symbol not in cache or str(cache[symbol].get("industry") or "UNKNOWN") == "UNKNOWN"
        ):
            cache[symbol] = {
                "raw_sector": "UNKNOWN",
                "raw_industry": "UNKNOWN",
                "industry": LEGACY_SECTOR_MAP[symbol],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "status": "manual_legacy",
            }
        elif symbol not in cache:
            try:
                cache[symbol] = fetch_yahoo_info(symbol)
            except Exception as exc:  # pragma: no cover - network-dependent
                cache[symbol] = {
                    "raw_sector": "UNKNOWN",
                    "raw_industry": "UNKNOWN",
                    "industry": "UNKNOWN",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "status": f"error: {type(exc).__name__}: {exc}",
                }
            fetched += 1
            write_cache(cache_path, cache)
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

        cached = cache.get(symbol, {})
        rows.append(
            {
                "Symbol": symbol,
                "Industry": str(cached.get("industry") or "UNKNOWN"),
                "Source": "yfinance",
            }
        )

    output = pl.DataFrame(rows).unique(subset=["Symbol"], keep="first").sort("Symbol")
    output.write_csv(output_path)
    write_cache(cache_path, cache)

    covered = output.filter(pl.col("Industry") != "UNKNOWN").height
    total = output.height
    print(
        json.dumps(
            {
                "signal_symbols": len(symbols),
                "base_nifty500_symbols": len(base),
                "output_symbols": total,
                "covered_symbols": covered,
                "coverage_pct": covered / total if total else 0.0,
                "fetched_from_yfinance": fetched,
                "output_path": str(output_path),
                "cache_path": str(cache_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
