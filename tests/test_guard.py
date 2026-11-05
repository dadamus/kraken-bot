"""The guard on every refresh (--ha): stop-loss / take-profit on the current price, no model."""
import pytest

from krakenbot import guard, kraken as kraken_api
from conftest import make_state

INFO = {"XBTEUR": {"key": "XXBTZEUR", "lot_decimals": 8, "ordermin": 0.0001}}


def btc_position(stop=95.0):
    return make_state(eur=0, XBTEUR={"coin": 1.0, "entry_price": 100.0, "stop_price": stop})


def test_paper_stop_sells_whole_position_once(cfg):
    state = btc_position()
    rows = guard.guard(cfg, state, {"XBTEUR": 94.0, "ETHEUR": 1.0, "SOLEUR": 1.0}, live=False)

    assert len(rows) == 1
    r = rows[0]
    assert (r["pair"], r["model_action"], r["executed"]) == ("XBTEUR", "stop", "sell")
    assert r["rule"] == "STOP (level 95)"
    assert state["positions"]["XBTEUR"]["coin"] == 0
    assert state["eur"] == pytest.approx(94 * 0.999 * 0.996)
    assert r["eur"] == pytest.approx(state["eur"], abs=0.01) and r["equity_eur"] == r["eur"]
    # the next refresh: the position is gone
    assert guard.guard(cfg, state, {"XBTEUR": 90.0}, live=False) == []


def test_take_profit_between_cycles(cfg):
    rows = guard.guard(cfg, btc_position(), {"XBTEUR": 116.0}, live=False)
    assert rows[0]["rule"] == "TAKE-PROFIT"


def test_nothing_happens_inside_range(cfg):
    state = btc_position()
    assert guard.guard(cfg, state, {"XBTEUR": 100.0}, live=False) == []
    assert state["positions"]["XBTEUR"]["coin"] == 1.0


def test_guard_can_be_disabled(cfg):
    cfg["risk"]["intraday_stops"] = False
    assert guard.guard(cfg, btc_position(), {"XBTEUR": 50.0}, live=False) == []


@pytest.fixture
def kraken(monkeypatch):
    """A fake Kraken: records orders; sync_live keeps or clears the position."""
    calls = {"orders": [], "sold_for_real": False}

    def live_execute(cfg, info, order, price):
        calls["orders"].append(order)
        return calls.get("accept", True)

    def sync_live(cfg, state, prices):
        if calls["sold_for_real"]:
            state["positions"]["XBTEUR"].update(coin=0.0, entry_price=None, stop_price=None)
        return INFO

    monkeypatch.setattr(kraken_api, "live_execute", live_execute)
    monkeypatch.setattr(kraken_api, "sync_live", sync_live)
    return calls


def test_live_validate_only_reports_once_a_day(cfg, kraken):
    state = btc_position()
    rows = guard.guard(cfg, state, {"XBTEUR": 94.0}, live=True, pair_info=INFO)

    assert len(rows) == 1 and "validation only" in rows[0]["rule"]
    assert state["positions"]["XBTEUR"]["coin"] == 1.0  # nothing changed on Kraken
    assert state["positions"]["XBTEUR"]["stop_price"] == 95.0
    # further refreshes that day: no repeats every 15 minutes
    assert guard.guard(cfg, state, {"XBTEUR": 94.0}, live=True, pair_info=INFO) == []
    # the next day it reports again
    state["guard_fired"]["XBTEUR"] = "2000-01-01"
    assert len(guard.guard(cfg, state, {"XBTEUR": 94.0}, live=True, pair_info=INFO)) == 1
    assert len(kraken["orders"]) == 2


def test_live_real_sale(cfg, kraken):
    cfg["live_validate_only"] = False
    kraken["sold_for_real"] = True
    state = btc_position()
    rows = guard.guard(cfg, state, {"XBTEUR": 94.0}, live=True, pair_info=INFO)

    assert rows[0]["executed"] == "sell" and "validation" not in rows[0]["rule"]
    assert kraken["orders"][0] == {"side": "sell", "coin": 1.0, "why": "STOP (level 95)"}
    assert state["positions"]["XBTEUR"]["coin"] == 0.0  # state from the real balance after the sale


def test_live_rejected_order_is_retried(cfg, kraken):
    cfg["live_validate_only"] = False
    kraken["accept"] = False
    state = btc_position()
    first = guard.guard(cfg, state, {"XBTEUR": 94.0}, live=True, pair_info=INFO)
    second = guard.guard(cfg, state, {"XBTEUR": 94.0}, live=True, pair_info=INFO)

    assert first[0]["executed"] == "error" and "order failed" in first[0]["rule"]
    assert len(second) == 1  # a rejected order is retried on the next refresh
