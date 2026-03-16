from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(slots=True)
class Signal:
    signal_date: date
    symbol: str
    signal_type: str
    close: float
    pivot: float
    near_pivot: float
    volume: int
    vol50: float
    vol_mult: float
    rs_rank: float
    range10: float
    voldry: float
    atr_pct: float
    adv50: float
    entry_ref: float
    stop: float
    r_per_share: float
    created_at: datetime


@dataclass(slots=True)
class RunLog:
    run_id: str
    started_at: datetime
    finished_at: datetime | None
    scan_date: date
    market_filter_on: bool | None
    num_symbols: int
    num_eligible: int
    num_watch: int
    num_breakout: int
    status: str
    error: str | None = None
    num_trend_pass: int = 0
    num_rs_pass: int = 0
    num_setup_pass: int = 0
    num_near_pivot: int = 0
    bhavcopy_sha256: str | None = None
    bhavcopy_url: str | None = None


@dataclass(slots=True)
class DownloadManifest:
    download_id: str
    trade_date: date
    report_name: str | None
    url: str | None
    source: str
    downloaded_at: datetime
    http_status: int | None
    content_length: int | None
    etag: str | None
    last_modified: str | None
    file_path: str | None
    sha256_zip: str | None
    rows_total: int | None = None
    rows_after_series_filter: int | None = None
    rows_after_dedupe: int | None = None
    duplicates_dropped: int | None = None
    parse_notes: str | None = None


@dataclass(slots=True)
class WatchlistCandidate:
    watch_date: date
    symbol: str
    close: float
    pivot: float
    near_pivot: float
    rs_rank: float
    range10: float
    voldry: float
    atr_pct: float
    adv50: float
    vol50: float
    created_at: datetime


@dataclass(slots=True)
class SetupReview:
    scan_date: date
    symbol: str
    review_status: str
    priority: int
    note: str
    reviewed_at: datetime
    updated_at: datetime
