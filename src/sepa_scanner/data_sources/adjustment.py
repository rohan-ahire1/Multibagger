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
    # An explicitly stated multiplier wins over anything derived. Inferred
    # overrides know the observed price ratio but not the face values behind
    # it, and a row whose factor cannot be derived is skipped silently --
    # leaving the discontinuity in the panel while the CA file claims it was
    # handled.
    explicit = row.get("adjustment_factor")
    if explicit is not None and float(explicit) > 0:
        return float(explicit)

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


def _factor_steps(ca: pl.DataFrame, *, volume: bool, key_column: str = "symbol") -> pl.DataFrame:
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
        rows.append({"symbol": r[key_column], "ex_date": r["ex_date"], "mult": m})

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


def _adjustment_key(frame: pl.DataFrame, *, action_frame: bool) -> pl.Expr:
    """Use ISIN when supplied; otherwise retain an explicitly-symbol fallback.

    A ticker is time-varying.  Matching on supplied ISIN preserves a rename as
    one economic security, while a missing historical ISIN is deliberately not
    linked to a later one merely because its ticker looks familiar.
    """
    isin = pl.col("isin").cast(pl.Utf8).str.strip_chars() if "isin" in frame.columns else pl.lit(None, dtype=pl.Utf8)
    has_isin = isin.is_not_null() & (isin != "")
    return pl.when(has_isin).then(pl.concat_str([pl.lit("isin:"), isin])).otherwise(
        pl.concat_str([pl.lit("symbol:"), pl.col("symbol").cast(pl.Utf8)])
    ).alias("_adjustment_key")


def apply_adjustment(
    raw_panel: pl.DataFrame,
    ca: pl.DataFrame,
    *,
    max_action_date: date | None = None,
) -> pl.DataFrame:
    """Return a back-adjusted panel with the app's expected OHLCV schema.

    ``raw_panel`` columns: date, symbol, [series, isin,] open, high, low, close,
    volume, turnover (raw). ``ca`` columns: symbol, ex_date, action_type,
    ratio_num, ratio_den, old_face_value, new_face_value, [dividend_amount, prev_close].
    """
    panel = raw_panel.sort(["symbol", "date"]).with_columns(_adjustment_key(raw_panel, action_frame=False))
    # A retrospective series may only be built from actions whose ex-date is
    # observable in the supplied price sample.  Without this cap a 2027 action
    # rewrites an otherwise final 2026 build; that is both non-reproducible and
    # a direct point-in-time leak if this output is accidentally used as input.
    dataset_max_date = max_action_date or panel["date"].max()
    if dataset_max_date is None:
        return panel
    if "ex_date" not in ca.columns:
        raise ValueError("corporate-action frame missing ex_date")
    effective_ca = ca.filter(pl.col("ex_date") <= pl.lit(dataset_max_date)).with_columns(
        _adjustment_key(ca, action_frame=True)
    )

    price_steps = _factor_steps(effective_ca, volume=False, key_column="_adjustment_key")
    vol_steps = _factor_steps(effective_ca, volume=True, key_column="_adjustment_key")

    def _join_factor(steps: pl.DataFrame, name: str) -> pl.DataFrame:
        if steps.is_empty():
            return panel.with_columns(pl.lit(1.0).alias(name))
        right = steps.rename({"symbol": "_adjustment_key", "ex_date": "date"}).sort(["_adjustment_key", "date"])
        joined = panel.sort(["_adjustment_key", "date"]).join_asof(
            right,
            on="date",
            by="_adjustment_key",
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
    adjusted = adjusted.drop("_adjustment_key")
    keep = [
        c
        for c in [
            "date", "security_id", "isin", "identity_quality", "series", "symbol",
            "open", "high", "low", "close", "volume", "turnover", "raw_price",
        ]
        if c in adjusted.columns
    ]
    sort_columns = [column for column in ("security_id", "symbol", "date") if column in keep]
    return adjusted.select(keep).sort(sort_columns)


def validate_share_action_ratios(
    raw_panel: pl.DataFrame,
    actions: pl.DataFrame,
    *,
    tolerance: float = 0.15,
) -> pl.DataFrame:
    """Compare split/bonus mechanics with unmodified raw closes.

    This is evidence reporting only: outside-tolerance events are classified
    for review and never fed back into the adjustment layer automatically.
    """
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")
    required_raw = {"date", "symbol", "close"}
    required_actions = {"symbol", "ex_date", "action_type"}
    if missing := required_raw - set(raw_panel.columns):
        raise ValueError(f"raw panel missing validation columns: {sorted(missing)}")
    if missing := required_actions - set(actions.columns):
        raise ValueError(f"corporate-action frame missing validation columns: {sorted(missing)}")

    action_types = {"split", "bonus", "bonus_split", "face_value", "consolidation"}
    raw = raw_panel.select("date", "symbol", "close", *( ["isin"] if "isin" in raw_panel.columns else [] )).with_columns(
        _adjustment_key(raw_panel, action_frame=False)
    )
    candidates = actions.filter(pl.col("action_type").is_in(sorted(action_types))).with_columns(
        _adjustment_key(actions, action_frame=True)
    )
    multiplicities = {
        (str(row["_adjustment_key"]), row["ex_date"]): int(row["events"])
        for row in candidates.group_by(["_adjustment_key", "ex_date"]).len().rename({"len": "events"}).iter_rows(named=True)
    }
    rows: list[dict[str, object]] = []
    for event in candidates.iter_rows(named=True):
        key = str(event["_adjustment_key"])
        event_date = event["ex_date"]
        frame = raw.filter(pl.col("_adjustment_key") == key).sort("date")
        expected = event_price_multiplier(event)
        security_id = key if key.startswith("isin:") else f"fallback:{event['symbol']}"
        base = {
            "security_id": security_id,
            "symbol": str(event["symbol"]),
            "event_date": event_date,
            "action": str(event["action_type"]),
            "expected_ratio": expected,
            "observed_raw_ratio": None,
            "deviation": None,
            "validation_status": "WARNING",
            "classification": "metadata_insufficient",
        }
        if expected is None or expected <= 0 or frame.is_empty():
            rows.append(base)
            continue
        before = frame.filter(pl.col("date") < pl.lit(event_date)).tail(1)
        after = frame.filter(pl.col("date") >= pl.lit(event_date)).head(1)
        if before.is_empty() or after.is_empty() or float(before["close"][0]) <= 0:
            rows.append(base)
            continue
        observed = float(after["close"][0]) / float(before["close"][0])
        deviation = abs(observed - expected) / abs(expected)
        if deviation <= tolerance:
            rows.append({
                **base, "observed_raw_ratio": observed, "deviation": deviation,
                "validation_status": "PASS", "classification": "within_tolerance",
            })
            continue
        if multiplicities[(key, event_date)] > 1:
            classification = "likely_multiple_simultaneous_actions"
        else:
            # Raw prices alone cannot prove whether the ratio metadata or the
            # observed price is wrong; do not guess between the two.
            classification = "unresolved"
        rows.append({
            **base, "observed_raw_ratio": observed, "deviation": deviation,
            "validation_status": "WARNING", "classification": classification,
        })
    schema = {
        "security_id": pl.Utf8, "symbol": pl.Utf8, "event_date": pl.Date,
        "action": pl.Utf8, "expected_ratio": pl.Float64, "observed_raw_ratio": pl.Float64,
        "deviation": pl.Float64, "validation_status": pl.Utf8, "classification": pl.Utf8,
    }
    return pl.DataFrame(rows, schema=schema).sort(["event_date", "security_id", "action"])
