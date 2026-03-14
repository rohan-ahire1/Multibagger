from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest


def test_load_corporate_actions_csv(tmp_path: Path) -> None:
    from sepa_scanner.data_sources.corporate_actions import load_corporate_actions_csv

    csv_content = (
        "symbol,isin,action_type,ex_date,ratio_num,ratio_den,"
        "old_face_value,new_face_value,adjustment_factor,description,source\n"
        "IRCTC,,split,2022-10-28,,,10,2,0.2,5:1 split FV 10->2,seed\n"
        "TCS,,bonus,2018-07-18,1,1,,,0.5,1:1 bonus,seed\n"
    )
    csv_path = tmp_path / "corp_actions.csv"
    csv_path.write_text(csv_content)

    df = load_corporate_actions_csv(csv_path)
    assert df.height == 2
    assert df["symbol"].to_list() == ["IRCTC", "TCS"]
    assert df["adjustment_factor"].to_list() == [0.2, 0.5]
    assert df["ex_date"].to_list() == [date(2022, 10, 28), date(2018, 7, 18)]
