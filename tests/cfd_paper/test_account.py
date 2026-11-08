"""The simulated CFD account: spread, P&L both ways, swap, margin and the stop-out."""
from datetime import timedelta

import pytest
from .cfd_helpers import NOW

from cfd import account


def test_round_trip_at_the_same_price_costs_exactly_the_spread(ccfg, cstate):
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 48000, 54000, NOW)
    tr = account.close_position(ccfg, cstate, "XBTEUR", 50000, "model: flat", NOW)
    assert tr["net_eur"] == pytest.approx(-0.01 * 50000 * 0.002, abs=0.01)  # 0.2 % round trip
    assert tr["spread_eur"] == pytest.approx(1.0, abs=0.01)
    assert cstate["balance"] == pytest.approx(999.0)
    assert cstate["costs"]["spread_eur"] == pytest.approx(1.0)


def test_short_profits_when_the_price_falls(ccfg, cstate):
    account.open_position(ccfg, cstate, "ETHEUR", "short", 1.0, 3000, 3150, 2700, NOW)
    eq = account.equity(ccfg, cstate, {"ETHEUR": 2900})
    assert eq == pytest.approx(1000 + (3000 * 0.999 - 2900 * 1.001))  # sold at the bid, bought back at the ask
    tr = account.close_position(ccfg, cstate, "ETHEUR", 2900, "TAKE-PROFIT", NOW + timedelta(days=2))
    assert tr["net_eur"] > 0
    assert tr["move_pct"] == pytest.approx(3.33, abs=0.01)  # in the short's direction
    assert tr["days"] == 2.0
    assert tr["r_multiple"] == pytest.approx(tr["net_eur"] / 150, abs=0.01)


def test_short_loses_when_the_price_rises(ccfg, cstate):
    account.open_position(ccfg, cstate, "ETHEUR", "short", 1.0, 3000, 3150, 2700, NOW)
    assert account.equity(ccfg, cstate, {"ETHEUR": 3100}) < 1000 - 100


def test_swap_is_charged_once_per_midnight_on_both_sides(ccfg, cstate):
    ccfg["swap_pct_per_day"] = {"long": 0.1, "short": 0.03}  # a short costs less to hold, as at XTB
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 48000, 54000, NOW)
    account.open_position(ccfg, cstate, "ETHEUR", "short", 0.1, 3000, 3150, 2700, NOW)
    assert account.charge_swaps(ccfg, cstate, {"XBTEUR": 50000, "ETHEUR": 3000}, NOW + timedelta(hours=6)) == 0
    fee = account.charge_swaps(ccfg, cstate, {"XBTEUR": 50000, "ETHEUR": 3000}, NOW + timedelta(days=3))
    assert fee == pytest.approx((500 * 0.001 + 300 * 0.0003) * 3)
    assert account.charge_swaps(ccfg, cstate, {"XBTEUR": 50000, "ETHEUR": 3000}, NOW + timedelta(days=3, hours=5)) == 0
    tr = account.close_position(ccfg, cstate, "XBTEUR", 50000, "model: flat", NOW + timedelta(days=3))
    assert tr["swap_eur"] == pytest.approx(1.5)
    assert tr["net_eur"] == pytest.approx(tr["pnl_eur"] - 1.5, abs=0.01)


def test_margin_is_notional_over_leverage(ccfg, cstate):
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.02, 50000, 48000, 54000, NOW)
    assert account.margin_used(ccfg, cstate, {"XBTEUR": 50000}) == pytest.approx(500)
    assert account.margin_level(ccfg, cstate, {"XBTEUR": 50000}) == pytest.approx(199.6, abs=0.01)  # equity after the full spread / 500 margin


def test_stop_out_closes_the_worst_position_until_the_margin_level_recovers(ccfg, cstate):
    ccfg["leverage"] = 10  # enough leverage to reach the stop-out
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.15, 50000, None, None, NOW)
    account.open_position(ccfg, cstate, "ETHEUR", "long", 0.5, 3000, None, None, NOW)
    prices = {"XBTEUR": 44500, "ETHEUR": 3000}  # BTC -11 %: equity ~165 EUR on 900 EUR margin
    rows = account.stop_out(ccfg, cstate, prices, NOW)
    assert [r["pair"] for r in rows] == ["XBTEUR"]
    assert rows[0]["reason"] == "STOP-OUT"
    assert "ETHEUR" in cstate["positions"]
    assert account.stop_out(ccfg, cstate, prices, NOW) == []


def test_state_survives_a_save_and_load(ccfg, cstate):
    account.open_position(ccfg, cstate, "XBTEUR", "short", 0.01, 50000, 52000, 46000, NOW)
    account.save_state(cstate)
    assert account.load_state(ccfg)["positions"]["XBTEUR"]["side"] == "short"
