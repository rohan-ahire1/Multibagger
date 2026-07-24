from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import polars as pl
import pytest

from sepa_scanner.data_sources.fundamentals import (
    apply_fundamental_gate,
    compute_earnings_acceleration,
    compute_eps_growth_gate,
    fetch_nse_financial_result_detail,
    fetch_nse_financial_result_filings,
    normalize_nse_financial_result_filings,
)


def test_eps_growth_gate_passes_strong_grower() -> None:
    earnings = pl.DataFrame({
        "symbol": ["ABC", "ABC"],
        "quarter_end": [date(2025, 9, 30), date(2025, 12, 31)],
        "eps": [12.0, 15.0],
        "revenue": [120.0, 150.0],
        "yoy_eps_growth": [0.20, 0.30],  # latest level 30% >= 25% default
    })
    res = compute_eps_growth_gate(earnings)
    assert res.filter(pl.col("symbol") == "ABC")["passes_filter"][0] is True


def test_eps_growth_gate_rejects_weak_grower_even_if_accelerating() -> None:
    earnings = pl.DataFrame({
        "symbol": ["XYZ", "XYZ"],
        "quarter_end": [date(2025, 9, 30), date(2025, 12, 31)],
        "eps": [10.0, 11.0],
        "revenue": [100.0, 110.0],
        "yoy_eps_growth": [0.05, 0.10],  # accelerating but level 10% < 25%
    })
    res = compute_eps_growth_gate(earnings)
    assert res.filter(pl.col("symbol") == "XYZ")["passes_filter"][0] is False


def test_eps_growth_gate_empty() -> None:
    res = compute_eps_growth_gate(pl.DataFrame(schema={
        "symbol": pl.String, "quarter_end": pl.Date, "eps": pl.Float64,
        "revenue": pl.Float64, "yoy_eps_growth": pl.Float64,
    }))
    assert res.is_empty()


def test_earnings_acceleration_detects_accelerating_growth() -> None:
    earnings = pl.DataFrame({
        "symbol": ["ABC", "ABC", "ABC"],
        "quarter_end": [date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)],
        "eps": [10.0, 12.0, 15.0],
        "revenue": [100.0, 120.0, 150.0],
        "yoy_eps_growth": [0.15, 0.20, 0.30],  # accelerating: 15% -> 20% -> 30%
    })

    result = compute_earnings_acceleration(earnings)
    abc = result.filter(pl.col("symbol") == "ABC")
    assert abc["passes_filter"][0] is True
    assert abc["acceleration"][0] > 0


def test_earnings_acceleration_rejects_decelerating_growth() -> None:
    earnings = pl.DataFrame({
        "symbol": ["XYZ", "XYZ", "XYZ"],
        "quarter_end": [date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)],
        "eps": [10.0, 12.0, 13.0],
        "revenue": [100.0, 120.0, 130.0],
        "yoy_eps_growth": [0.30, 0.25, 0.22],  # decelerating: 30% -> 25% -> 22%
    })

    result = compute_earnings_acceleration(earnings)
    xyz = result.filter(pl.col("symbol") == "XYZ")
    assert xyz["passes_filter"][0] is False


def test_empty_earnings_returns_empty_result() -> None:
    result = compute_earnings_acceleration(pl.DataFrame(schema={
        "symbol": pl.String,
        "quarter_end": pl.Date,
        "eps": pl.Float64,
        "revenue": pl.Float64,
        "yoy_eps_growth": pl.Float64,
    }))
    assert result.is_empty()


def test_apply_fundamental_gate_point_in_time(tmp_path) -> None:
    import polars as pl
    from sepa_scanner.data_sources.fundamentals import apply_fundamental_gate
    # earnings: ABC strong (30%), DEF weak (5%); known 75d after period_end
    earn = pl.DataFrame({
        "symbol": ["ABC", "DEF"],
        "period_end": [date(2023, 3, 31), date(2023, 3, 31)],
        "freq": ["annual", "annual"],
        "yoy_growth": [0.30, 0.05],
    })
    p = tmp_path / "earn.csv"
    earn.write_csv(p)
    signals = pl.DataFrame({
        "signal_date": [date(2023, 9, 1), date(2023, 9, 1), date(2023, 9, 1)],
        "symbol": ["ABC", "DEF", "GHI"],  # GHI has no data
        "rs_rank": [80.0, 80.0, 80.0],
    })
    out = apply_fundamental_gate(signals, data_path=p, min_yoy_growth=0.25, freq="annual")
    kept = set(out["symbol"].to_list())
    assert kept == {"ABC", "GHI"}  # ABC passes, DEF dropped, GHI kept (no data)


def test_apply_fundamental_gate_respects_reporting_lag(tmp_path) -> None:
    import polars as pl
    from sepa_scanner.data_sources.fundamentals import apply_fundamental_gate
    earn = pl.DataFrame({
        "symbol": ["ABC"], "period_end": [date(2023, 3, 31)],
        "freq": ["annual"], "yoy_growth": [0.30],
    })
    p = tmp_path / "e.csv"
    earn.write_csv(p)
    # signal BEFORE results are known (Apr 2023, lag 75d -> known mid-Jun) -> no data -> kept
    signals = pl.DataFrame({"signal_date": [date(2023, 4, 1)], "symbol": ["ABC"], "rs_rank": [80.0]})
    out = apply_fundamental_gate(signals, data_path=p, min_yoy_growth=0.25, reporting_lag_days=75, freq="annual")
    assert out.height == 1  # kept via pass_if_missing (results not yet public)


def test_apply_fundamental_gate_uses_actual_available_date(tmp_path) -> None:
    earnings = pl.DataFrame({
        "symbol": ["ABC"],
        "period_end": [date(2023, 3, 31)],
        "available_date": [date(2023, 5, 20)],
        "freq": ["annual"],
        "yoy_growth": [0.05],
    })
    path = tmp_path / "actual_dates.csv"
    earnings.write_csv(path)
    signals = pl.DataFrame({
        "signal_date": [date(2023, 5, 19), date(2023, 5, 20)],
        "symbol": ["ABC", "ABC"],
        "marker": ["before", "on_date"],
    })

    result = apply_fundamental_gate(
        signals,
        data_path=path,
        min_yoy_growth=0.25,
        require_actual_announcement_date=True,
    )

    assert result["marker"].to_list() == ["before"]


def test_apply_fundamental_gate_strict_mode_rejects_lag_proxy(tmp_path) -> None:
    path = tmp_path / "legacy.csv"
    pl.DataFrame({
        "symbol": ["ABC"],
        "period_end": [date(2023, 3, 31)],
        "yoy_growth": [0.30],
    }).write_csv(path)
    signals = pl.DataFrame({
        "signal_date": [date(2023, 9, 1)],
        "symbol": ["ABC"],
    })

    with pytest.raises(ValueError, match="actual announcement column"):
        apply_fundamental_gate(
            signals,
            data_path=path,
            require_actual_announcement_date=True,
        )


def test_normalize_nse_filings_respects_scanner_cutoff() -> None:
    rows = [
        {
            "symbol": "EARLY",
            "companyName": "Early Limited",
            "period": "Quarterly",
            "toDate": "31-Mar-2026",
            "filingDate": "15-May-2026 15:30",
            "broadCastDate": "15-May-2026 16:00:00",
            "seqNumber": "1",
        },
        {
            "symbol": "LATE",
            "companyName": "Late Limited",
            "period": "Quarterly",
            "toDate": "31-Mar-2026",
            "filingDate": "15-May-2026 16:11",
            "broadCastDate": "15-May-2026 16:12:00",
            "seqNumber": "2",
        },
    ]

    result = normalize_nse_financial_result_filings(rows, scan_cutoff_ist=time(16, 10))
    available = dict(zip(result["symbol"].to_list(), result["available_date"].to_list()))

    assert available == {
        "EARLY": date(2026, 5, 15),
        "LATE": date(2026, 5, 16),
    }
    assert result.filter(pl.col("symbol") == "LATE")["filing_timestamp"].item() == datetime(
        2026, 5, 15, 16, 12
    )


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeSession:
    def __init__(self, payloads: list[Any]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return _FakeResponse(self.payloads.pop(0))


def test_fetch_nse_filings_chunks_long_ranges() -> None:
    row = {
        "symbol": "ABC",
        "companyName": "ABC Limited",
        "period": "Quarterly",
        "toDate": "31-Mar-2026",
        "filingDate": "15-May-2026 14:00",
        "broadCastDate": "15-May-2026 14:00:01",
        "seqNumber": "101",
    }
    session = _FakeSession([[row], [row]])

    result = fetch_nse_financial_result_filings(
        date(2026, 4, 1),
        date(2026, 5, 15),
        session=session,  # type: ignore[arg-type]
    )

    assert len(session.calls) == 2
    assert result.height == 1
    assert result["available_date"].item() == date(2026, 5, 15)


def test_fetch_nse_financial_result_detail_parses_headline_values() -> None:
    session = _FakeSession([{
        "filingDate": "15-May-2026 16:30",
        "longname": "ABC Limited",
        "periodEndDT": "31-Mar-2026",
        "seqnum": "101",
        "resultsData2": {
            "re_proloss_ord_act": "(1,234.50)",
            "re_net_sale": "8,765.25",
            "re_basic_eps_for_cont_dic_opr": "12.75",
        },
    }])
    filing = {
        "symbol": "ABC",
        "seq_number": "101",
        "params": "opaque",
        "industry": "-",
        "old_new_flag": "N",
        "re_ind": "N",
        "format": "New",
    }

    result = fetch_nse_financial_result_detail(
        filing,
        session=session,  # type: ignore[arg-type]
    )

    assert result["net_profit"] == -1234.5
    assert result["revenue"] == 8765.25
    assert result["eps"] == 12.75
    assert result["available_date"] == date(2026, 5, 16)
    assert session.calls[0]["params"]["seq_id"] == "101"
