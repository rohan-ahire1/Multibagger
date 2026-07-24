"""Deterministic corporate-action back-adjustment for the raw NSE bhavcopy panel.

Back-adjustment convention (matches yfinance Adj Close semantics):
- The most recent bar is left at raw prices (cumulative factor = 1.0).
- Every bar *strictly before* a corporate-action ex-date is scaled by that action's
  price multiplier, so the series is continuous across splits/bonuses/(dividends).

Price multipliers ``m`` (applied to all dates < ex_date):
- Split / face-value split (FV old -> new):  m = new_fv / old_fv
- Bonus a:b (a new per b held):              m = b / (a + b)
- Dividend D (Phase 2, needs prev_close):    m = 1 - D / prev_close

Volume is inverse-adjusted for splits/bonuses only (not dividends):
    adjusted_volume = raw_volume / volume_factor
Turnover is kept RAW (raw_close * raw_volume) and documented as such -- it is the only
internally-inconsistent column and callers must not treat it as adjusted traded value.
"""
from __future__ import annotations

from datetime import date

import polars as pl

# Actions that move price only (no share-count change) -> price factor but not volume.
_PRICE_ONLY = {"dividend"}
# Actions that change share count -> both price and volume factors.
_SHARE_ACTIONS = {"split", "bonus", "face_value", "bonus_split"}

_ANCHOR_DATE = date(1990, 1, 1)


def _fv_multiplier(row: dict) -> float | None:
    old_fv, new_fv = row.get("old_face_value"), row.get("new_face_value")
    if old_fv and new_fv and old_fv > 0:
        return float(new_fv) / float(old_fv)
    return None


def _bonus_multiplier(row: dict) -> float | None:
    num, den = row.get("ratio_num"), row.get("ratio_den")  # "Bonus a:b" -> a new per b held
    if num is not None and den:
        a, b = float(num), float(den)
        if (a + b) > 0:
            return b / (a + b)
    return None


def event_price_multiplier(row: dict) -> float | None:
    """Price multiplier for one CA event (applied to all dates before ex-date).

    Combined bonus+split subjects multiply both legs so no residual cliff remains.
    """
    action = (row.get("action_type") or "").lower()
    if action in {"split", "face_value"}:
        m = _fv_multiplier(row)
        if m is not None:
            return m
        num, den = row.get("ratio_num"), row.get("ratio_den")
        if num and den and num > 0:  # split ratio new:old face value
            return float(den) / float(num)
        return None
    if action == "bonus":
        return _bonus_multiplier(row)
    if action == "bonus_split":
        fv = _fv_multiplier(row)
        bon = _bonus_multiplier(row)
        if fv is None and bon is None:
            return None
        return (fv or 1.0) * (bon or 1.0)
    if action == "dividend":
        amt, prev = row.get("dividend_amount"), row.get("prev_close")
        if amt and prev and prev > 0:
            m = 1.0 - float(amt) / float(prev)
            return m if m > 0 else None
        return None
    return None


def _factor_steps(ca: pl.DataFrame, *, volume: bool) -> pl.DataFrame:
    """Per-symbol step table: ``factor`` applies to all dates >= ``ex_date``.

    Returns columns ``symbol, ex_date, factor``. ``factor`` for a date t equals the
    product of every event multiplier with ex_date > t (back-adjustment).
    """
    rows: list[dict] = []
    for r in ca.iter_rows(named=True):
        m = event_price_multiplier(r)
        if m is None or m <= 0:
            continue
        action = (r.get("action_type") or "").lower()
        if volume and action in _PRICE_ONLY:
            continue  # dividends do not move volume
        rows.append({"symbol": r["symbol"], "ex_date": r["ex_date"], "mult": m})

    steps: list[dict] = []
    if rows:
        ev = pl.DataFrame(rows).sort(["symbol", "ex_date"])
        for symbol, grp in ev.group_by("symbol", maintain_order=True):
            mults = grp["mult"].to_list()
            ex_dates = grp["ex_date"].to_list()
            full = 1.0
            for m in mults:
                full *= m
            # factor for dates < first ex-date = product of all mults
            steps.append({"symbol": symbol[0], "ex_date": _ANCHOR_DATE, "factor": full})
            prefix = 1.0
            for ex, m in zip(ex_dates, mults):
                prefix *= m
                # for dates >= ex, this event no longer applies
                steps.append({"symbol": symbol[0], "ex_date": ex, "factor": full / prefix})
    schema = {"symbol": pl.Utf8, "ex_date": pl.Date, "factor": pl.Float64}
    return pl.DataFrame(steps, schema=schema).sort(["symbol", "ex_date"])


def apply_adjustment(raw_panel: pl.DataFrame, ca: pl.DataFrame) -> pl.DataFrame:
    """Return a back-adjusted panel with the app's expected OHLCV schema.

    ``raw_panel`` columns: date, symbol, [series, isin,] open, high, low, close,
    volume, turnover (raw). ``ca`` columns: symbol, ex_date, action_type,
    ratio_num, ratio_den, old_face_value, new_face_value, [dividend_amount, prev_close].
    """
    panel = raw_panel.sort(["symbol", "date"])

    price_steps = _factor_steps(ca, volume=False)
    vol_steps = _factor_steps(ca, volume=True)

    def _join_factor(steps: pl.DataFrame, name: str) -> pl.DataFrame:
        if steps.is_empty():
            return panel.with_columns(pl.lit(1.0).alias(name))
        right = steps.rename({"ex_date": "date"}).sort(["symbol", "date"])
        joined = panel.sort(["symbol", "date"]).join_asof(
            right,
            on="date",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
        return joined.with_columns(pl.col("factor").fill_null(1.0).alias(name)).drop("factor")

    panel = _join_factor(price_steps, "price_factor")
    panel = _join_factor(vol_steps, "volume_factor")

    adjusted = panel.with_columns(
        (pl.col("open") * pl.col("price_factor")).alias("open"),
        (pl.col("high") * pl.col("price_factor")).alias("high"),
        (pl.col("low") * pl.col("price_factor")).alias("low"),
        (pl.col("close") * pl.col("price_factor")).alias("close"),
        (pl.col("volume") / pl.col("volume_factor")).round(0).cast(pl.Int64).alias("volume"),
        # turnover intentionally left raw (documented inconsistency)
    )
    keep = [
        c
        for c in ["date", "symbol", "open", "high", "low", "close", "volume", "turnover"]
        if c in adjusted.columns
    ]
    return adjusted.select(keep).sort(["symbol", "date"])
