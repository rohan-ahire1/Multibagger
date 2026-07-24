from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .engine import DEFAULT_OUTPUT, ResearchConfig, run_research


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare daily cross-sectional factor portfolios with SEPA")
    parser.add_argument("--start", type=date.fromisoformat, default=ResearchConfig.start)
    parser.add_argument("--end", type=date.fromisoformat, default=ResearchConfig.end)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = run_research(ResearchConfig(start=args.start, end=args.end), args.output)
    print("\nStrategy                              CAGR    MaxDD  Calmar")
    print("-" * 62)
    rows = payload["results"] + [payload["benchmark"]]
    if payload["sepa"]:
        rows.append(payload["sepa"])
    for row in sorted(rows, key=lambda item: float(item["calmar"]), reverse=True):
        print(
            f"{row['strategy']:<36} {float(row['cagr'])*100:>6.2f}% "
            f"{float(row['max_drawdown'])*100:>7.2f}% {float(row['calmar']):>7.2f}"
        )
    print(f"\nWrote research outputs to {args.output}")


if __name__ == "__main__":
    main()
