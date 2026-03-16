from __future__ import annotations

import polars as pl
from rich.console import Console
from rich.table import Table


class ConsoleAlertChannel:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def send(self, signals: pl.DataFrame, *, market_filter_on: bool) -> None:
        if signals.is_empty():
            self.console.print("No signals.")
            return

        table = Table(title=f"NSE Scanner Alerts | MarketFilter={market_filter_on}")
        for column in ["symbol", "signal_type", "close", "pivot", "rs_rank", "vol_mult", "near_pivot"]:
            table.add_column(column)

        for row in signals.sort(["signal_type", "symbol"]).to_dicts():
            table.add_row(
                row["symbol"],
                row["signal_type"],
                f"{row['close']:.2f}",
                f"{row['pivot']:.2f}",
                f"{row['rs_rank']:.0f}",
                f"{row['vol_mult']:.2f}",
                f"{row['near_pivot']:.2%}",
            )

        self.console.print(table)
