from __future__ import annotations

from collections.abc import Mapping

import requests

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Referer": "https://www.nseindia.com/",
}


def build_nse_session(extra_headers: Mapping[str, str] | None = None) -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    if extra_headers:
        session.headers.update(extra_headers)
    return session


def bootstrap_nse_session(
    session: requests.Session,
    *,
    timeout: int = 15,
) -> requests.Session:
    try:
        session.get("https://www.nseindia.com/", timeout=timeout)
    except requests.RequestException:
        pass
    return session
