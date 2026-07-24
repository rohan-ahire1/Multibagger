from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import requests

from sepa_scanner.utils.http import bootstrap_nse_session, build_nse_session


NSE_FINANCIAL_RESULTS_URL = "https://www.nseindia.com/api/corporates-financial-results"
NSE_FINANCIAL_RESULTS_DATA_URL = "https://www.nseindia.com/api/corporates-financial-results-data"
NSE_SCAN_CUTOFF_IST = time(16, 10)


_NSE_FILING_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.String,
    "company_name": pl.String,
    "period": pl.String,
    "period_end": pl.Date,
    "filing_received_timestamp": pl.Datetime,
    "filing_timestamp": pl.Datetime,
    "available_date": pl.Date,
    "audited": pl.String,
    "consolidated": pl.String,
    "cumulative": pl.String,
    "financial_year": pl.String,
    "relating_to": pl.String,
    "seq_number": pl.String,
    "params": pl.String,
    "industry": pl.String,
    "old_new_flag": pl.String,
    "re_ind": pl.String,
    "format": pl.String,
    "xbrl_url": pl.String,
}


def _parse_nse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if value in (None, "", "-"):
        return None
    text = str(value).strip()
    for fmt in (
        "%d-%b-%Y %H:%M:%S",
        "%d-%b-%Y %H:%M",
        "%d-%b-%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_nse_date(value: Any) -> date | None:
    parsed = _parse_nse_datetime(value)
    return parsed.date() if parsed is not None else None


def _available_date(filing_timestamp: datetime | None, scan_cutoff_ist: time) -> date | None:
    """Map an NSE dissemination timestamp to the first scanner decision date.

    NSE timestamps are published in IST without a timezone suffix. A result
    disseminated after the end-of-day scanner cutoff cannot affect that day's
    signals, so it becomes available on the following calendar date. The
    signal as-of join naturally advances weekends/holidays to the next session.
    """
    if filing_timestamp is None:
        return None
    day = filing_timestamp.date()
    return day if filing_timestamp.time() <= scan_cutoff_ist else day + timedelta(days=1)


def normalize_nse_financial_result_filings(
    rows: Iterable[Mapping[str, Any]],
    *,
    scan_cutoff_ist: time = NSE_SCAN_CUTOFF_IST,
) -> pl.DataFrame:
    """Normalize NSE's financial-results listing into point-in-time metadata.

    ``filing_timestamp`` uses the exchange broadcast/dissemination timestamp,
    not the reporting-period end date. Timestamps remain naive because NSE's
    endpoint reports local IST values without an offset.
    """
    normalized: list[dict[str, Any]] = []
    for row in rows:
        received_at = _parse_nse_datetime(row.get("filingDate"))
        disseminated_at = _parse_nse_datetime(
            row.get("broadCastDate") or row.get("broadcastDate")
        ) or received_at
        normalized.append(
            {
                "symbol": row.get("symbol"),
                "company_name": row.get("companyName"),
                "period": row.get("period"),
                "period_end": _parse_nse_date(row.get("toDate")),
                "filing_received_timestamp": received_at,
                "filing_timestamp": disseminated_at,
                "available_date": _available_date(disseminated_at, scan_cutoff_ist),
                "audited": row.get("audited"),
                "consolidated": row.get("consolidated"),
                "cumulative": row.get("cumulative"),
                "financial_year": row.get("financialYear"),
                "relating_to": row.get("relatingTo"),
                "seq_number": str(row["seqNumber"]) if row.get("seqNumber") is not None else None,
                "params": row.get("params"),
                "industry": row.get("industry"),
                "old_new_flag": row.get("oldNewFlag"),
                "re_ind": row.get("reInd"),
                "format": row.get("format"),
                "xbrl_url": row.get("xbrl"),
            }
        )
    return pl.from_dicts(normalized, schema=_NSE_FILING_SCHEMA, strict=False)


def fetch_nse_financial_result_filings(
    start_date: date,
    end_date: date,
    *,
    period: str = "Quarterly",
    index: str = "equities",
    session: requests.Session | None = None,
    timeout: int = 30,
) -> pl.DataFrame:
    """Fetch NSE financial-result filings in bounded, inclusive date chunks."""
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    client = session
    if client is None:
        client = bootstrap_nse_session(build_nse_session(), timeout=timeout)

    raw_rows: list[Mapping[str, Any]] = []
    chunk_start = start_date
    while chunk_start <= end_date:
        chunk_end = min(chunk_start + timedelta(days=30), end_date)
        response = client.get(
            NSE_FINANCIAL_RESULTS_URL,
            params={
                "index": index,
                "period": period,
                "from_date": chunk_start.strftime("%d-%m-%Y"),
                "to_date": chunk_end.strftime("%d-%m-%Y"),
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            raw_rows.extend(row for row in payload if isinstance(row, Mapping))
        elif isinstance(payload, Mapping):
            data = payload.get("data", [])
            if isinstance(data, list):
                raw_rows.extend(row for row in data if isinstance(row, Mapping))
        chunk_start = chunk_end + timedelta(days=1)

    result = normalize_nse_financial_result_filings(raw_rows)
    if result.is_empty():
        return result
    return result.unique(
        subset=["symbol", "period_end", "filing_timestamp", "seq_number"],
        keep="last",
        maintain_order=True,
    ).sort(["filing_timestamp", "symbol"])


def _parse_result_number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    text = str(value).strip().replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def _first_result_number(values: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        number = _parse_result_number(values.get(key))
        if number is not None:
            return number
    return None


def fetch_nse_financial_result_detail(
    filing: Mapping[str, Any],
    *,
    index: str = "equities",
    session: requests.Session | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Fetch the headline values for one NSE financial-result filing."""
    client = session
    if client is None:
        client = bootstrap_nse_session(build_nse_session(), timeout=timeout)

    def field(normalized: str, raw: str) -> Any:
        return filing.get(normalized, filing.get(raw))

    response = client.get(
        NSE_FINANCIAL_RESULTS_DATA_URL,
        params={
            "index": index,
            "params": field("params", "params"),
            "seq_id": field("seq_number", "seqNumber"),
            "industry": field("industry", "industry"),
            "frOldNewFlag": field("old_new_flag", "oldNewFlag"),
            "ind": field("re_ind", "reInd"),
            "format": field("format", "format"),
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("NSE financial-result detail returned a non-object payload")

    values = payload.get("resultsData2") or payload.get("resultsData") or {}
    if isinstance(values, list):
        values = values[0] if values and isinstance(values[0], Mapping) else {}
    if not isinstance(values, Mapping):
        values = {}

    filing_timestamp = _parse_nse_datetime(
        field("filing_timestamp", "broadCastDate") or payload.get("filingDate")
    )
    available = field("available_date", "availableDate")
    available_date = _parse_nse_date(available) or _available_date(
        filing_timestamp, NSE_SCAN_CUTOFF_IST
    )
    return {
        "symbol": field("symbol", "symbol"),
        "company_name": field("company_name", "companyName") or payload.get("longname"),
        "period_end": _parse_nse_date(payload.get("periodEndDT") or field("period_end", "toDate")),
        "filing_timestamp": filing_timestamp,
        "available_date": available_date,
        "seq_number": str(payload.get("seqnum") or field("seq_number", "seqNumber") or "") or None,
        "net_profit": _first_result_number(
            values,
            ("re_proloss_ord_act", "re_con_pro_loss", "dis_opr_aftr_tax_plus_ord_act"),
        ),
        "revenue": _first_result_number(values, ("re_net_sale", "re_total_inc")),
        "eps": _first_result_number(
            values,
            ("re_basic_eps_for_cont_dic_opr", "re_bsc_eps_bfr_exi", "re_basic_eps"),
        ),
    }


def load_quarterly_results(path: Path | None = None) -> pl.DataFrame:
    """Load quarterly earnings data.

    Expected columns: symbol, quarter_end, eps, revenue, yoy_eps_growth.
    Returns empty DataFrame if path is None or doesn't exist.
    """
    if path is None or not Path(path).exists():
        return pl.DataFrame(schema={
            "symbol": pl.String,
            "quarter_end": pl.Date,
            "eps": pl.Float64,
            "revenue": pl.Float64,
            "yoy_eps_growth": pl.Float64,
        })
    return pl.read_csv(path, try_parse_dates=True)


def apply_fundamental_gate(
    signals: pl.DataFrame,
    *,
    data_path: str | Path,
    min_yoy_growth: float = 0.25,
    reporting_lag_days: int = 75,
    freq: str | None = None,
    pass_if_missing: bool = True,
    availability_date_column: str = "available_date",
    require_actual_announcement_date: bool = False,
) -> pl.DataFrame:
    """Filter breakout signals by point-in-time earnings growth (Minervini gate).

    Point-in-time: when ``availability_date_column`` exists, a quarter/year only
    counts from its actual exchange dissemination date. The reporting-lag proxy
    is retained solely for non-strict legacy research files. In strict mode,
    missing or invalid actual availability metadata raises instead of silently
    introducing look-ahead.

    The data file may use either schema (annual: period_end/yoy_growth;
    quarterly: quarter_end/yoy_eps_growth). Returns ``signals`` unchanged with a
    no-op (and warning) if the file is missing in legacy mode. Strict mode fails
    closed. Signals with no available data are kept iff ``pass_if_missing``.
    """
    path = Path(data_path)
    if not path.exists():
        if require_actual_announcement_date:
            raise FileNotFoundError(f"point-in-time fundamental data not found: {path}")
        print(f"⚠️  fundamental_gate enabled but {path} not found — gate skipped (no-op).")
        return signals
    if "signal_date" not in signals.columns or "symbol" not in signals.columns:
        return signals
    if signals.is_empty():
        return signals

    e = pl.read_csv(path, try_parse_dates=True)
    date_col = "period_end" if "period_end" in e.columns else "quarter_end"
    growth_col = "yoy_growth" if "yoy_growth" in e.columns else "yoy_eps_growth"
    required = {"symbol", date_col, growth_col}
    missing = required.difference(e.columns)
    if missing:
        raise ValueError(f"fundamental data missing required columns: {sorted(missing)}")
    if freq and "freq" in e.columns:
        e = e.filter(pl.col("freq") == freq)

    has_actual_availability = availability_date_column in e.columns
    if require_actual_announcement_date and not has_actual_availability:
        raise ValueError(
            "strict point-in-time fundamental gate requires actual announcement "
            f"column {availability_date_column!r}"
        )

    selected = e.select(
        "symbol",
        pl.col(date_col).cast(pl.Date, strict=False).alias("_pe"),
        pl.col(growth_col).cast(pl.Float64, strict=False).alias("_g"),
        *(
            [pl.col(availability_date_column).cast(pl.Date, strict=False).alias("_actual_known")]
            if has_actual_availability
            else []
        ),
    )
    if has_actual_availability:
        if require_actual_announcement_date and selected.filter(
            pl.col("_g").is_not_null() & pl.col("_actual_known").is_null()
        ).height:
            raise ValueError("strict point-in-time fundamental data has missing announcement dates")
        selected = selected.with_columns(
            (
                pl.col("_actual_known")
                if require_actual_announcement_date
                else pl.coalesce(
                    pl.col("_actual_known"),
                    pl.col("_pe") + pl.duration(days=reporting_lag_days),
                )
            ).alias("_known")
        )
    else:
        selected = selected.with_columns(
            (pl.col("_pe") + pl.duration(days=reporting_lag_days)).alias("_known")
        )

    invalid_dates = selected.filter(
        pl.col("_known").is_not_null()
        & pl.col("_pe").is_not_null()
        & (pl.col("_known") < pl.col("_pe"))
    )
    if invalid_dates.height:
        raise ValueError("fundamental announcement date precedes its reporting period end")

    e = selected.select("symbol", "_known", "_g").drop_nulls(["_known", "_g"])
    if e.is_empty():
        return signals if pass_if_missing else signals.head(0)
    e = e.sort(["symbol", "_known"])

    cols = signals.columns
    joined = signals.with_row_index("_fundamental_order").sort(["symbol", "signal_date"]).join_asof(
        e.select(["symbol", "_known", "_g"]),
        left_on="signal_date",
        right_on="_known",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    cond = pl.col("_g") >= min_yoy_growth
    if pass_if_missing:
        cond = pl.col("_g").is_null() | cond
    return joined.filter(cond).sort("_fundamental_order").select(cols)


def compute_eps_growth_gate(
    earnings: pl.DataFrame, min_yoy_growth: float = 0.25
) -> pl.DataFrame:
    """Minervini-style EPS-growth-level research gate.

    Uses the latest YoY EPS growth *level*, not acceleration. The default 0.25
    is Minervini's classic +25% threshold. Corrected portfolio testing on the
    current partial, survivorship-biased local dataset did not improve the
    canonical strategy, so this helper is not evidence to enable the live gate.

    Returns: symbol, latest_quarter, yoy_growth, passes_filter.
    """
    if earnings.is_empty():
        return pl.DataFrame(schema={
            "symbol": pl.String,
            "latest_quarter": pl.Date,
            "yoy_growth": pl.Float64,
            "passes_filter": pl.Boolean,
        })
    return (
        earnings.sort(["symbol", "quarter_end"])
        .group_by("symbol")
        .agg(pl.all().sort_by("quarter_end").last())
        .select(
            "symbol",
            pl.col("quarter_end").alias("latest_quarter"),
            pl.col("yoy_eps_growth").alias("yoy_growth"),
        )
        .with_columns(
            (pl.col("yoy_growth") >= min_yoy_growth).fill_null(False).alias("passes_filter")
        )
    )


def compute_earnings_acceleration(earnings: pl.DataFrame) -> pl.DataFrame:
    """Compute earnings acceleration: QoQ change in YoY EPS growth rate.

    NOTE: prototype testing found acceleration carries NO forward signal on
    Indian data; prefer compute_eps_growth_gate (EPS-growth level). Kept for
    reference / comparison.

    Returns DataFrame with columns: symbol, latest_quarter, yoy_growth, acceleration, passes_filter.
    A stock passes if: yoy_growth >= 20% AND acceleration > 0 (growth is accelerating).
    """
    if earnings.is_empty():
        return pl.DataFrame(schema={
            "symbol": pl.String,
            "latest_quarter": pl.Date,
            "yoy_growth": pl.Float64,
            "acceleration": pl.Float64,
            "passes_filter": pl.Boolean,
        })

    # Sort by symbol and quarter, compute acceleration
    sorted_df = earnings.sort(["symbol", "quarter_end"])

    result = sorted_df.with_columns(
        pl.col("yoy_eps_growth").shift(1).over("symbol").alias("prev_yoy_growth"),
    ).with_columns(
        (pl.col("yoy_eps_growth") - pl.col("prev_yoy_growth")).alias("acceleration"),
    )

    # Get latest quarter per symbol
    latest = (
        result.group_by("symbol")
        .agg(pl.all().sort_by("quarter_end").last())
        .select(
            "symbol",
            pl.col("quarter_end").alias("latest_quarter"),
            pl.col("yoy_eps_growth").alias("yoy_growth"),
            "acceleration",
        )
    ).with_columns(
        (
            (pl.col("yoy_growth") >= 0.20)
            & (pl.col("acceleration") > 0)
        ).alias("passes_filter"),
    )

    return latest
