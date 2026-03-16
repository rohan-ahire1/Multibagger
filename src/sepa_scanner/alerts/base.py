from __future__ import annotations

from typing import Protocol

import polars as pl


class AlertChannel(Protocol):
    def send(self, signals: pl.DataFrame, *, market_filter_on: bool) -> None:
        """Deliver scanner signals."""
