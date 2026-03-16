from __future__ import annotations

import polars as pl


def rank_signals(frame: pl.DataFrame, max_alerts_per_day: int) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    return (
        frame.sort(
            by=["signal_type", "rs_rank", "vol_mult", "range10"],
            descending=[False, True, True, False],
        )
        .group_by("signal_type", maintain_order=True)
        .head(max_alerts_per_day)
    )
