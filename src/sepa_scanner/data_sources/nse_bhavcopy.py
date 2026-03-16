from __future__ import annotations

import tempfile
from datetime import date
from io import BytesIO
from pathlib import Path
import logging
import re
import time
from zipfile import ZipFile

import polars as pl
import requests
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from sepa_scanner.settings import AppSettings
from sepa_scanner.utils.hashing import maybe_archive_previous, sha256_file
from sepa_scanner.utils.http import build_nse_session
from sepa_scanner.utils.io import ensure_directory
from sepa_scanner.utils.validation import valid_ohlcv_rows

from dataclasses import dataclass

ARCHIVE_BASE_URL = "https://nsearchives.nseindia.com/content/cm"
LEGACY_ARCHIVE_BASE_URL = "https://nsearchives.nseindia.com/content/historical/EQUITIES"
DISCOVERY_URL = "https://www.nseindia.com/api/merged-daily-reports"
DISCOVERY_NAME = "CM-UDiFF Common Bhavcopy Final (zip)"
DATE_PATTERN = re.compile(r"_(\d{8})_[FP]_0000\.csv\.zip$")
LEGACY_CUTOFF = date(2024, 1, 1)
LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DownloadMeta:
    url: str
    http_status: int | None
    content_length: int | None
    etag: str | None
    last_modified: str | None
    sha256_zip: str
    was_cached: bool


def build_bhavcopy_url(trade_date: date, final: bool = True) -> str:
    suffix = "F" if final else "P"
    return (
        f"{ARCHIVE_BASE_URL}/BhavCopy_NSE_CM_0_0_0_"
        f"{trade_date:%Y%m%d}_{suffix}_0000.csv.zip"
    )


def build_legacy_bhavcopy_url(trade_date: date) -> str:
    month_abbr = trade_date.strftime("%b").upper()
    return (
        f"{LEGACY_ARCHIVE_BASE_URL}/{trade_date.year}/{month_abbr}/"
        f"cm{trade_date:%d}{month_abbr}{trade_date.year}bhav.csv.zip"
    )


def _parse_trade_date() -> pl.Expr:
    return pl.coalesce(
        [
            pl.col("TradDt").str.strptime(pl.Date, format="%d-%b-%Y", strict=False),
            pl.col("TradDt").str.strptime(pl.Date, format="%Y-%m-%d", strict=False),
            pl.col("TradDt").str.strptime(pl.Date, format="%d-%m-%Y", strict=False),
        ]
    )


def parse_bhavcopy_zip(zip_path: Path, allowed_series: list[str]) -> pl.DataFrame:
    with ZipFile(zip_path) as archive:
        first_member = archive.namelist()[0]
        payload = archive.read(first_member)

    frame = pl.read_csv(
        BytesIO(payload),
        infer_schema_length=200,
        ignore_errors=False,
    )

    normalized = (
        frame.with_columns(
            date=_parse_trade_date(),
            symbol=pl.col("TckrSymb").cast(pl.Utf8).str.strip_chars(),
            series=pl.col("SctySrs").cast(pl.Utf8).str.strip_chars(),
            open=pl.col("OpnPric").cast(pl.Float64),
            high=pl.col("HghPric").cast(pl.Float64),
            low=pl.col("LwPric").cast(pl.Float64),
            close=pl.col("ClsPric").cast(pl.Float64),
            volume=pl.col("TtlTradgVol").cast(pl.Int64),
            turnover=pl.col("TtlTrfVal").cast(pl.Float64),
        )
        .select(
            "date",
            "symbol",
            "series",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
        )
        .filter(pl.col("series").is_in(allowed_series))
        .filter(valid_ohlcv_rows())
        .unique(subset=["date", "symbol"], keep="first", maintain_order=True)
        .sort(["symbol", "date"])
    )

    return normalized


def parse_legacy_bhavcopy_zip(zip_path: Path, allowed_series: list[str]) -> pl.DataFrame:
    """Parse pre-2024 NSE bhavcopy format (cm*bhav.csv.zip)."""
    with ZipFile(zip_path) as archive:
        first_member = archive.namelist()[0]
        payload = archive.read(first_member)

    frame = pl.read_csv(
        BytesIO(payload),
        infer_schema_length=200,
        ignore_errors=False,
    )

    col_map = {c: c.strip() for c in frame.columns}
    frame = frame.rename(col_map)

    normalized = (
        frame.with_columns(
            date=pl.coalesce([
                pl.col("TIMESTAMP").str.strptime(pl.Date, format="%d-%b-%y", strict=False),
                pl.col("TIMESTAMP").str.strptime(pl.Date, format="%d-%b-%Y", strict=False),
                pl.col("TIMESTAMP").str.strptime(pl.Date, format="%Y-%m-%d", strict=False),
            ]),
            symbol=pl.col("SYMBOL").cast(pl.Utf8).str.strip_chars(),
            series=pl.col("SERIES").cast(pl.Utf8).str.strip_chars(),
            open=pl.col("OPEN").cast(pl.Float64),
            high=pl.col("HIGH").cast(pl.Float64),
            low=pl.col("LOW").cast(pl.Float64),
            close=pl.col("CLOSE").cast(pl.Float64),
            volume=pl.col("TOTTRDQTY").cast(pl.Int64),
            turnover=pl.col("TOTTRDVAL").cast(pl.Float64),
        )
        .select(
            "date", "symbol", "series",
            "open", "high", "low", "close",
            "volume", "turnover",
        )
        .filter(pl.col("series").is_in(allowed_series))
        .filter(valid_ohlcv_rows())
        .unique(subset=["date", "symbol"], keep="first", maintain_order=True)
        .sort(["symbol", "date"])
    )

    return normalized


class NseBhavcopyClient:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.session = build_nse_session()
        self._bootstrapped = False

    def _request(self, url: str, **kwargs) -> requests.Response:
        timeout = kwargs.pop("timeout", self.settings.data.http_timeout_sec)
        retryer = Retrying(
            stop=stop_after_attempt(self.settings.data.max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(requests.RequestException),
            reraise=True,
        )
        for attempt in retryer:
            with attempt:
                response = self.session.get(url, timeout=timeout, **kwargs)
                if response.status_code == 404:
                    return response
                response.raise_for_status()
                return response
        raise RuntimeError("unreachable")

    def bootstrap(self) -> None:
        if self._bootstrapped:
            return
        try:
            self.session.get(
                "https://www.nseindia.com/",
                timeout=self.settings.data.http_timeout_sec,
            )
        except requests.RequestException:
            LOGGER.debug("NSE homepage bootstrap failed; continuing with direct API requests.")
        self._bootstrapped = True

    def discover_latest_report(self) -> tuple[date, str]:
        url = f"{DISCOVERY_URL}?key={self.settings.data.bhavcopy_api_key}"
        try:
            response = self._request(url, headers={"Accept": "application/json"})
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 403:
                raise
            self.bootstrap()
            response = self._request(url, headers={"Accept": "application/json"})
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Unexpected merged-daily-reports payload")
        for item in payload:
            if item.get("name") == DISCOVERY_NAME and item.get("link"):
                url = str(item["link"])
                trade_date = self.trade_date_from_url(url)
                return trade_date, url
        raise ValueError(f"Discovery payload did not include {DISCOVERY_NAME}")

    def trade_date_from_url(self, url: str) -> date:
        match = DATE_PATTERN.search(url)
        if match is None:
            raise ValueError(f"Could not parse trade date from {url}")
        token = match.group(1)
        return date(int(token[0:4]), int(token[4:6]), int(token[6:8]))

    def download_url(
        self, url: str, destination: Path, *, force: bool = False
    ) -> tuple[Path, DownloadMeta]:
        ensure_directory(destination.parent)

        if destination.exists() and not force:
            sha = sha256_file(destination)
            return destination, DownloadMeta(
                url=url,
                http_status=None,
                content_length=None,
                etag=None,
                last_modified=None,
                sha256_zip=sha,
                was_cached=True,
            )

        response = self._request(url, headers={"Accept": "*/*"})
        if response.status_code == 404:
            raise FileNotFoundError(url)

        with tempfile.NamedTemporaryFile(
            dir=destination.parent, suffix=".tmp", delete=False
        ) as tmp:
            tmp.write(response.content)
            tmp_path = Path(tmp.name)

        sha = sha256_file(tmp_path)
        maybe_archive_previous(destination, sha)
        tmp_path.rename(destination)

        headers = response.headers
        content_length = headers.get("Content-Length")

        return destination, DownloadMeta(
            url=url,
            http_status=response.status_code,
            content_length=int(content_length) if content_length else len(response.content),
            etag=headers.get("ETag"),
            last_modified=headers.get("Last-Modified"),
            sha256_zip=sha,
            was_cached=False,
        )

    def download_latest(
        self, raw_dir: Path, *, force: bool = False
    ) -> tuple[date, Path, DownloadMeta]:
        trade_date, url = self.discover_latest_report()
        target = raw_dir / f"{trade_date.isoformat()}.zip"
        path, meta = self.download_url(url, target, force=force)
        return trade_date, path, meta

    def download_for_date(
        self,
        trade_date: date,
        raw_dir: Path,
        *,
        final_only: bool = True,
        force: bool = False,
        sleep_seconds: float = 0.0,
    ) -> tuple[Path, DownloadMeta] | None:
        target = raw_dir / f"{trade_date.isoformat()}.zip"

        if trade_date < LEGACY_CUTOFF:
            try:
                result = self.download_url(
                    build_legacy_bhavcopy_url(trade_date), target, force=force
                )
                if sleep_seconds:
                    time.sleep(sleep_seconds)
                return result
            except FileNotFoundError:
                return None

        suffixes = [True] if final_only else [True, False]
        for final in suffixes:
            try:
                result = self.download_url(
                    build_bhavcopy_url(trade_date, final=final), target, force=force
                )
                if sleep_seconds:
                    time.sleep(sleep_seconds)
                return result
            except FileNotFoundError:
                continue
        return None
