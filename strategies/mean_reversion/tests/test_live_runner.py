from __future__ import annotations

from datetime import date

import pytest

from strategies.mean_reversion.live.friction import buy_cash_outflow, sell_cash_inflow
from strategies.mean_reversion.live.paper_portfolio import append_paper_fills, process_paper_fills
from strategies.mean_reversion.live.portfolio_state import LivePosition, PortfolioState


def test_portfolio_state_round_trip(tmp_path) -> None:
    path = tmp_path / "state.json"
    state = PortfolioState.empty()
    state.add_position(
        LivePosition(
            symbol="TEST",
            entry_date=date(2026, 4, 20),
            entry_price=100.0,
            stop=90.0,
            active_stop=90.0,
            shares=10.0,
            initial_risk=10.0,
            cost_basis=1000.0,
            rsi2_at_entry=8.0,
            atr14=3.0,
            is_paper=True,
        )
    )
    state.save(path)

    loaded = PortfolioState.load(path)
    assert loaded.cash == state.cash
    assert loaded.positions[0].symbol == "TEST"
    assert loaded.positions[0].entry_date == date(2026, 4, 20)
    assert loaded.open_symbols() == {"TEST"}
    assert loaded.equity({"TEST": 110.0}) == loaded.cash + 1100.0


def test_close_position_returns_trade_and_updates_cash() -> None:
    state = PortfolioState.empty()
    start_cash = state.cash
    state.add_position(
        LivePosition(
            symbol="TEST",
            entry_date=date(2026, 4, 20),
            entry_price=100.0,
            stop=95.0,
            active_stop=95.0,
            shares=5.0,
            initial_risk=5.0,
            cost_basis=500.0,
            rsi2_at_entry=10.0,
            atr14=2.0,
            is_paper=False,
        )
    )

    trade = state.close_position("TEST", 110.0, date(2026, 4, 22), "mean_revert_sma3")

    # Sell-side friction: slippage + statutory charges + flat DP charge.
    _, _, _, net_proceeds = sell_cash_inflow(5.0, 110.0)
    expected_pnl = net_proceeds - 500.0
    assert trade["pnl"] == pytest.approx(expected_pnl)
    assert trade["pnl"] < 50.0  # costs eat into the raw 50.0 gross gain
    assert trade["r_multiple"] == pytest.approx(expected_pnl / (5.0 * 5.0))
    assert state.cash == pytest.approx(start_cash + expected_pnl)
    assert state.positions == []


def test_process_paper_fills_uses_today_open_and_clears_pending() -> None:
    state = PortfolioState.empty()
    pending = [
        {
            "symbol": "TEST",
            "signal_date": "2026-04-19",
            "stop": 95.0,
            "shares": 4.0,
            "rsi2": 7.0,
            "atr14": 2.5,
        }
    ]
    symbol_data = {
        "TEST": {
            "date_to_idx": {date(2026, 4, 20): 0},
            "opens": [101.0],
        }
    }

    fills = process_paper_fills(state, symbol_data, date(2026, 4, 20), pending)

    # Buy-side slippage pushes the fill price above the raw open.
    fill_price, charges, cost_basis = buy_cash_outflow(4.0, 101.0)
    assert len(fills) == 1
    assert fills[0]["entry_price"] == pytest.approx(fill_price)
    assert fills[0]["entry_price"] > 101.0
    assert fills[0]["cost_basis"] == pytest.approx(cost_basis)
    assert fills[0]["entry_charges"] == pytest.approx(charges)
    assert state.positions[0].entry_price == pytest.approx(fill_price)
    assert state.pending_entries == []


def test_friction_round_trip_costs_are_realistic() -> None:
    # 100 shares at Rs 1000 = Rs 1,00,000 notional.
    shares, raw = 100.0, 1000.0
    buy_fill, buy_charges, cash_out = buy_cash_outflow(shares, raw)
    sell_fill, sell_charges, dp_charge, cash_in = sell_cash_inflow(shares, raw)

    # Slippage moves fills adversely by 0.05% each side (Dhan-accurate).
    assert buy_fill == pytest.approx(1000.5)
    assert sell_fill == pytest.approx(999.5)
    # Buy charges ~0.1187% of notional; sell charges ~0.1037% + flat Rs 14.75 DP.
    assert buy_charges == pytest.approx(100_050.0 * 0.00118658)
    assert sell_charges == pytest.approx(99_950.0 * 0.00103658)
    assert dp_charge == 14.75
    # Total round-trip drag on Rs 1L: slippage (0.1%) + statutory charges + DP.
    round_trip_cost = (cash_out - shares * raw) + (shares * raw - cash_in)
    assert 250.0 < round_trip_cost < 450.0  # ~Rs 337, ~0.34% all-in


def test_append_paper_fills_appends_without_overwriting(tmp_path) -> None:
    path = tmp_path / "paper_trades.csv"
    fill = {
        "fill_date": "2026-04-20",
        "symbol": "TEST",
        "entry_price": 101.0,
        "shares": 4.0,
        "cost_basis": 404.0,
        "stop": 95.0,
        "initial_risk": 6.0,
        "rsi2_at_entry": 7.0,
        "atr14": 2.5,
    }

    append_paper_fills([fill], path)
    append_paper_fills([fill], path)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("fill_date,symbol")
    assert lines[1] == lines[2]

