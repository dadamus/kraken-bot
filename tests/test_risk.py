"""Risk rules: stop levels, protective exits, order sizing, simulated execution, the drawdown halt."""
import pytest

import bot
from conftest import make_state, signal

PRICES = {"XBTEUR": 100.0, "ETHEUR": 100.0, "SOLEUR": 100.0}


# ---------------------------------------------------------------- stop_level
@pytest.mark.parametrize("model_stop, atr, expected", [
    (None, 2.0, 96.0),    # no level from the model: 2 ATR below the price
    (97.0, 2.0, 97.0),    # the model's level within limits
    (99.0, 2.0, 98.0),    # too close - moved to 1 ATR so noise does not shake us out
    (50.0, 2.0, 92.0),    # too far - at most stop_loss_pct (8%)
    (101.0, 2.0, 96.0),   # above the price makes no sense - same as no level
    (None, 10.0, 92.0),   # ATR larger than 8% - the limit wins
    (95.0, None, 95.0),   # without ATR: the model's level if within limits
    (50.0, None, 92.0),
    (None, None, 92.0),
])
def test_stop_level(cfg, model_stop, atr, expected):
    assert bot.stop_level(cfg, 100.0, atr, model_stop) == pytest.approx(expected)


# ---------------------------------------------------------------- tighten_stop
def test_position_without_stop_gets_first_stop(cfg):
    pos = {"coin": 1.0, "stop_price": None}
    assert bot.tighten_stop(cfg, pos, signal(stop=95.0), 100.0, atr=2.0) == 95.0
    assert pos["stop_price"] == 95.0


def test_first_stop_respects_limits(cfg):
    pos = {"coin": 1.0, "stop_price": None}
    assert bot.tighten_stop(cfg, pos, signal(stop=50.0), 100.0, atr=2.0) == 92.0


def test_stop_can_only_go_up(cfg):
    pos = {"coin": 1.0, "stop_price": 95.0}
    assert bot.tighten_stop(cfg, pos, signal(stop=90.0), 100.0) is None
    assert pos["stop_price"] == 95.0
    assert bot.tighten_stop(cfg, pos, signal(stop=97.0), 100.0) == 97.0
    assert pos["stop_price"] == 97.0


@pytest.mark.parametrize("pos, stop", [
    ({"coin": 0.0, "stop_price": None}, 95.0),   # no position
    ({"coin": 1.0, "stop_price": 95.0}, 101.0),  # stop above the price
    ({"coin": 1.0, "stop_price": 95.0}, None),   # the model gave no level
])
def test_tighten_stop_ignores(cfg, pos, stop):
    assert bot.tighten_stop(cfg, pos, signal(stop=stop), 100.0) is None


# ---------------------------------------------------------------- protective_exit
@pytest.mark.parametrize("pos, price, why", [
    ({"coin": 1, "entry_price": 100, "stop_price": 95}, 94.9, "STOP (level 95)"),
    ({"coin": 1, "entry_price": 100, "stop_price": None}, 92.0, "STOP-LOSS"),   # fixed -8% from entry
    ({"coin": 1, "entry_price": 100, "stop_price": 80}, 91.9, "STOP-LOSS"),     # also below the model's stop
    ({"coin": 1, "entry_price": 100, "stop_price": 95}, 115.0, "TAKE-PROFIT"),
])
def test_protective_exit_sells_everything(cfg, pos, price, why):
    order = bot.protective_exit(cfg, pos, price)
    assert order == {"side": "sell", "coin": 1, "why": why}


@pytest.mark.parametrize("pos, price", [
    ({"coin": 1, "entry_price": 100, "stop_price": 95}, 96.0),
    ({"coin": 0, "entry_price": None, "stop_price": None}, 1.0),
    ({"coin": 1, "entry_price": None, "stop_price": None}, 1.0),  # no entry price = no fixed stop
])
def test_protective_exit_keeps_position(cfg, pos, price):
    assert bot.protective_exit(cfg, pos, price) is None


def test_take_profit_can_be_disabled(cfg):
    cfg["risk"]["take_profit_pct"] = 0
    assert bot.protective_exit(cfg, {"coin": 1, "entry_price": 100}, 500.0) is None


# ---------------------------------------------------------------- decide
def test_hold_and_weak_signals_do_nothing(cfg):
    state = make_state()
    assert bot.decide(cfg, state, "XBTEUR", PRICES, signal("hold"))["side"] == "none"
    weak = bot.decide(cfg, state, "XBTEUR", PRICES, signal("buy", confidence=0.6))
    assert weak["side"] == "none" and "threshold" in weak["why"]


def test_buy_is_capped_by_max_trade(cfg):
    order = bot.decide(cfg, make_state(), "XBTEUR", PRICES, signal("buy", size_pct=50), atr=2.0)
    assert order["side"] == "buy" and order["eur"] == 200  # 50% of 1000 = 500, limit 200
    assert order["stop_price"] == 96.0 and "stop 96" in order["why"]


def test_buy_is_capped_by_pair_exposure(cfg):
    state = make_state(eur=750, XBTEUR={"coin": 2.5, "entry_price": 100})  # 250 EUR in BTC, limit 30% = 300
    order = bot.decide(cfg, state, "XBTEUR", PRICES, signal("buy", size_pct=100))
    assert order["eur"] == pytest.approx(50)


def test_no_buy_when_total_exposure_full(cfg):
    state = make_state(eur=400, ETHEUR={"coin": 3, "entry_price": 100}, SOLEUR={"coin": 3, "entry_price": 100})
    order = bot.decide(cfg, state, "XBTEUR", PRICES, signal("buy", size_pct=100))
    assert order["side"] == "none" and "limit" in order["why"]


def test_no_buy_below_minimum(cfg):
    order = bot.decide(cfg, make_state(), "XBTEUR", PRICES, signal("buy", size_pct=0.5))
    assert order["side"] == "none"


def test_sell_part_of_position(cfg):
    state = make_state(eur=0, XBTEUR={"coin": 2.0, "entry_price": 90})
    order = bot.decide(cfg, state, "XBTEUR", PRICES, signal("sell", size_pct=25))
    assert order == {"side": "sell", "coin": 0.5, "why": "model signal"}


def test_sell_without_position_does_nothing(cfg):
    order = bot.decide(cfg, make_state(), "XBTEUR", PRICES, signal("sell", size_pct=100))
    assert order["side"] == "none"


# ---------------------------------------------------------------- paper_execute
def test_paper_buy_and_sell_with_fees(cfg):
    state = make_state()
    fill, coin = bot.paper_execute(cfg, state, "XBTEUR", {"side": "buy", "eur": 200, "stop_price": 96}, 100.0)
    pos = state["positions"]["XBTEUR"]
    assert fill == pytest.approx(100.1)  # 0.1% slippage
    assert coin == pytest.approx(200 * 0.996 / 100.1)  # 0.4% fee
    assert state["eur"] == 800
    assert pos["entry_price"] == pytest.approx(200 / coin)  # the entry cost includes fees
    assert pos["stop_price"] == 96

    bot.paper_execute(cfg, state, "XBTEUR", {"side": "sell", "coin": pos["coin"]}, 110.0)
    assert state["eur"] == pytest.approx(800 + coin * 110 * 0.999 * 0.996)
    assert pos == {"coin": 0.0, "entry_price": None, "stop_price": None}


def test_second_buy_averages_entry_and_replaces_stop(cfg):
    state = make_state()
    bot.paper_execute(cfg, state, "XBTEUR", {"side": "buy", "eur": 100, "stop_price": 90}, 100.0)
    bot.paper_execute(cfg, state, "XBTEUR", {"side": "buy", "eur": 100, "stop_price": 110}, 120.0)
    pos = state["positions"]["XBTEUR"]
    assert pos["entry_price"] == pytest.approx(200 / pos["coin"])
    assert 100 < pos["entry_price"] < 120
    assert pos["stop_price"] == 110


def test_partial_sell_keeps_stop(cfg):
    state = make_state(eur=0, XBTEUR={"coin": 2.0, "entry_price": 100, "stop_price": 95})
    bot.paper_execute(cfg, state, "XBTEUR", {"side": "sell", "coin": 1.0}, 100.0)
    assert state["positions"]["XBTEUR"]["coin"] == 1.0
    assert state["positions"]["XBTEUR"]["stop_price"] == 95


# ---------------------------------------------------------------- check_halt
def test_drawdown_halts_buying(cfg):
    state = make_state(eur=790)  # -21% from the start
    reason = bot.check_halt(cfg, state, PRICES)
    assert "drawdown" in reason and state["halted_until"]
    state["eur"] = 1000  # the pause continues even after a rebound
    assert "halted" in bot.check_halt(cfg, state, PRICES)


def test_no_halt_on_small_loss(cfg):
    state = make_state(eur=900)
    assert bot.check_halt(cfg, state, PRICES) is None and state["halted_until"] is None
