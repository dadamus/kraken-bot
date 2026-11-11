"""Break-even: after breakeven_r x the initial risk in profit the stop moves to where closing gets the entry
price back after the spread."""
from datetime import timedelta

import pytest
from .cfd_helpers import NOW

from cfd import account, cycle, guard

T0 = int(NOW.timestamp())


def candle(t, o, h, low, c):
    return {"t": t, "o": o, "h": h, "l": low, "c": c}


def run(cfg, state, candles, prices=None, minutes=10):
    return guard.check(cfg, state, prices or {}, NOW + timedelta(minutes=minutes), lambda *a: (candles, 1))


def test_long_stop_moves_to_break_even_after_one_r_and_closes_flat(ccfg, cstate):
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 48000, 60000, NOW)  # 1R = 2000
    rows = run(ccfg, cstate, [candle(T0 + 60, 50000, 52100, 50000, 52000),
                              candle(T0 + 120, 52000, 52000, 50000, 50000)])
    assert rows[0]["reason"] == "STOP"
    assert rows[0]["exit"] == pytest.approx(rows[0]["entry"], rel=1e-6)  # entry back after the spread
    assert rows[0]["net_eur"] == pytest.approx(0, abs=0.01)


def test_short_stop_moves_to_break_even(ccfg, cstate):
    account.open_position(ccfg, cstate, "ETHEUR", "short", 1, 3000, 3100, 2700, NOW)
    rows = run(ccfg, cstate, [candle(T0 + 60, 3000, 3000, 2890, 2900),
                              candle(T0 + 120, 2900, 3000, 2900, 3000)])
    assert rows[0]["reason"] == "STOP" and rows[0]["net_eur"] == pytest.approx(0, abs=0.01)


def test_not_yet_one_r_keeps_the_stop(ccfg, cstate):
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 48000, 60000, NOW)
    run(ccfg, cstate, [candle(T0 + 60, 50000, 51900, 50000, 51800)])
    assert cstate["positions"]["XBTEUR"]["stop"] == 48000


def test_the_move_applies_from_the_next_candle(ccfg, cstate):
    """Inside one candle the order of the high and the low is unknown: no break-even exit in that candle."""
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 48000, 60000, NOW)
    rows = run(ccfg, cstate, [candle(T0 + 60, 50000, 52100, 49000, 49500)])
    assert rows == []
    assert cstate["positions"]["XBTEUR"]["stop"] == pytest.approx(50100.1, rel=1e-5)
    assert cstate["positions"]["XBTEUR"]["breakeven"] is True


def test_a_tighter_stop_is_not_loosened(ccfg, cstate):
    pos = account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 48000, 60000, NOW)
    pos["stop"] = 51000  # the model already moved it above break-even
    run(ccfg, cstate, [candle(T0 + 60, 51500, 52500, 51500, 52000)])
    assert cstate["positions"]["XBTEUR"]["stop"] == 51000


def test_off_with_zero(ccfg, cstate):
    ccfg["risk"]["breakeven_r"] = 0
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 48000, 60000, NOW)
    run(ccfg, cstate, [candle(T0 + 60, 50000, 55000, 50000, 54000)])
    assert cstate["positions"]["XBTEUR"]["stop"] == 48000


def test_the_model_sees_the_rule_and_the_moved_stop(ccfg, cstate):
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 48000, 60000, NOW)
    run(ccfg, cstate, [candle(T0 + 60, 50000, 52100, 50500, 52000)])
    assert cycle.trading_terms(ccfg, NOW)["breakeven_r"] == 1
    view = cycle.account_view(ccfg, cstate, {"XBTEUR": 52000}, NOW)
    assert view["positions"]["XBTEUR"]["stop_at_breakeven"] is True
