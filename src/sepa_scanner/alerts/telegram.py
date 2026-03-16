from __future__ import annotations

import logging

import polars as pl
import requests

from sepa_scanner.alerts.formatters import format_breakout_message, format_watch_message
from sepa_scanner.settings import AppSettings


LOGGER = logging.getLogger(__name__)


class TelegramAlertChannel:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.session = requests.Session()

    def send(self, signals: pl.DataFrame, *, market_filter_on: bool) -> None:
        if not self.settings.telegram.enabled:
            LOGGER.info("Telegram is disabled; skipping Telegram alerts.")
            return
        for row in signals.sort(["signal_type", "symbol"]).to_dicts():
            if row["signal_type"] == "WATCH":
                message = format_watch_message(row)
            else:
                message = format_breakout_message(
                    row, self.settings, market_filter_on=market_filter_on
                )
            self.session.post(
                f"https://api.telegram.org/bot{self.settings.telegram.bot_token}/sendMessage",
                data={
                    "chat_id": self.settings.telegram.chat_id,
                    "text": message,
                },
                timeout=self.settings.data.http_timeout_sec,
            ).raise_for_status()
