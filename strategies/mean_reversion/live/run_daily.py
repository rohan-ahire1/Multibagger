from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from strategies.mean_reversion.live.config import DATA_DIR, LIVE_STATE_PATH, PAPER_STATE_PATH
from strategies.mean_reversion.live.notifier import format_daily_report, send_daily_report
from strategies.mean_reversion.live.paper_portfolio import append_paper_fills
from strategies.mean_reversion.live.portfolio_state import PortfolioState
from strategies.mean_reversion.live.signal_runner import run_daily_signals


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the mean reversion live daily workflow.")
    parser.add_argument("--date", help="Override run date as YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Print report only; skip Telegram and state writes")
    parser.add_argument("--skip-update", action="store_true", help="Skip yfinance daily update before scanning")
    return parser.parse_args()


def parse_run_date(value: str | None) -> date:
    if value is None:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def main() -> None:
    args = parse_args()
    run_date = parse_run_date(args.date)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    live_state = PortfolioState.load(LIVE_STATE_PATH)
    paper_state = PortfolioState.load(PAPER_STATE_PATH)
    result = run_daily_signals(run_date, live_state, paper_state, update_data=not args.skip_update)
    report = format_daily_report(result)
    print(report)

    if args.dry_run:
        return

    live_state.save(LIVE_STATE_PATH)
    paper_state.save(PAPER_STATE_PATH)
    append_paper_fills(result.paper_fills)
    send_daily_report(result)


if __name__ == "__main__":
    main()

