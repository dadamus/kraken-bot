"""Live mode against a fake Kraken Derivatives: the opt-ins, real orders with resting stops, the exchange as the
source of truth, failures that must never leave a leveraged position without a stop, winding down, the smoke
test."""
import json
from datetime import timedelta

import pytest
from .cfd_helpers import NOW, fake_markets, sig
from .kraken_fake import FakeFutures, iso

from cfd import account, cycle, futures, journal, live, settings

SPOT = {"XBTEUR": 55000.0, "PAXGEUR": 3300.0}


@pytest.fixture
def kraken(monkeypatch):
    fake = FakeFutures().install(monkeypatch)
    monkeypatch.setattr(live.market, "fetch_price", lambda pair: {"EURUSD": 1.09, **SPOT}[pair])
    monkeypatch.setattr(cycle.guard, "fetch_candles", lambda *a: ([], 1))
    return fake


@pytest.fixture
def lcfg(ccfg):
    ccfg.update(pairs=["XBTEUR", "PAXGEUR"], mode="live")
    return ccfg


def start(lcfg):
    broker = live.prepare(lcfg)
    return broker, account.load_state(lcfg)


def cycle_with(lcfg, state, broker, calls, now, prices=None, monkeypatch=None):
    monkeypatch.setattr(cycle, "load_markets", fake_markets(prices or SPOT))
    cycle.daily_cycle(lcfg, state, now=now, broker=broker, mode="live", ask=lambda c, p: (calls, "s"))


def test_live_needs_both_opt_ins_a_key_and_eea_contracts(ccfg, monkeypatch):
    fake = FakeFutures().install(monkeypatch, keys=False)
    ccfg["pairs"] = ["XBTEUR", "PAXGEUR"]
    with pytest.raises(live.LiveError, match='"mode": "live"'):
        live.prepare(ccfg)
    ccfg["mode"] = "live"
    with pytest.raises(live.LiveError, match="derivatives key"):
        live.prepare(ccfg)
    fake.install(monkeypatch, keys=True)
    ccfg["pairs"].append("EURUSD")
    with pytest.raises(live.LiveError, match="EURUSD: PF_EURUSD is not offered to EEA clients"):
        live.prepare(ccfg)
    assert futures.ALLOW == "none" and not settings.LIVE


def test_prepare_adapts_the_instruments_and_switches_files(lcfg, kraken):
    broker = live.prepare(lcfg)
    gold = account.instrument(lcfg, "PAXGEUR")
    assert gold["leverage"] == 10 and account.leverage(lcfg, "XBTEUR") == 2  # Kraken's EEA cap; 2 stays 2
    assert gold["swap_pct_per_day"] == {"long": 0.0, "short": 0.0} and gold["weekend_close"] is None
    assert settings.LIVE and settings.STATE_FILE == settings.LIVE_STATE_FILE and futures.ALLOW == "all"
    assert broker.live


def test_open_rests_a_stop_and_take_profit_on_kraken(lcfg, kraken, monkeypatch, no_data):
    broker, state = start(lcfg)
    cycle_with(lcfg, state, broker, {"XBTEUR": sig("long", 0.9), "PAXGEUR": sig("hold")}, NOW,
               monkeypatch=monkeypatch)
    assert state["start_eur"] == pytest.approx(1000.0)  # the wallet's value at the first live run
    ex = kraken.positions["PF_XBTUSD"]
    pos = state["positions"]["XBTEUR"]
    assert ex["side"] == "long" and ex["size"] == pos["size"] == pos["qty"]
    assert pos["entry_usd"] == pytest.approx(60002) and pos["symbol"] == "PF_XBTUSD"
    stop, tp = kraken.orders[pos["stop_order"]], kraken.orders[pos["tp_order"]]
    assert stop["orderType"] == "stp" and stop["side"] == "sell" and stop["reduceOnly"]
    assert stop["triggerSignal"] == "mark" and tp["orderType"] == "take_profit"
    # the paper stop (2 ATR = 6 % under 55000) at the same distance under the contract's mid
    assert stop["stopPrice"] == round(pos["stop"] * 60000 / 55000) and tp["stopPrice"] > 60000
    assert journal.read_calls()[0]["executed"] == "open_long"
    assert settings.CALLS_FILE.name == "calls_live.csv"
    assert account.equity(lcfg, state, {**SPOT, "EURUSD": 1.09}) == pytest.approx(kraken.value() / 1.09, abs=0.5)


def test_a_stop_that_cannot_rest_closes_the_position_at_once(lcfg, kraken, monkeypatch, no_data):
    broker, state = start(lcfg)
    kraken.fail.add("stp")
    cycle_with(lcfg, state, broker, {"XBTEUR": sig("long", 0.9), "PAXGEUR": sig("hold")}, NOW,
               monkeypatch=monkeypatch)
    assert kraken.positions == {} and state["positions"] == {}
    call = journal.read_calls()[0]
    assert call["executed"] == "error" and "could not be placed" in call["rule"]
    assert journal.read_trades()[0]["reason"] == "NO STOP"
    assert "closed at once" in state["problems"]["live_stop_XBTEUR"]


def test_stop_fired_on_kraken_is_recorded_with_its_fill(lcfg, kraken, monkeypatch, no_data):
    broker, state = start(lcfg)
    cycle_with(lcfg, state, broker, {"XBTEUR": sig("long", 0.9), "PAXGEUR": sig("hold")}, NOW,
               monkeypatch=monkeypatch)
    pos = state["positions"]["XBTEUR"]
    stop_usd, tp_id = pos["stop_usd"], pos["tp_order"]
    kraken.move("PF_XBTUSD", stop_usd - 50)
    monkeypatch.setattr(cycle.market, "fetch_price", lambda pair: {"XBTEUR": 51000.0, "PAXGEUR": 3300.0, "EURUSD": 1.09}[pair])
    cycle.refresh(lcfg, state, now=NOW + timedelta(hours=1), broker=broker, mode="live")
    tr = journal.read_trades()[0]
    assert tr["reason"] == "STOP" and float(tr["exit"]) == stop_usd and tr["symbol"] == "PF_XBTUSD"
    assert float(tr["net_eur"]) < 0 and float(tr["fee_eur"]) > 0
    assert state["positions"] == {} and tp_id not in kraken.orders  # the orphaned take-profit is cancelled
    assert account.equity(lcfg, state, {**SPOT, "EURUSD": 1.09}) == pytest.approx(kraken.value() / 1.09, abs=0.01)


def test_breakeven_and_the_models_stop_edit_the_resting_order(lcfg, kraken, monkeypatch, no_data):
    lcfg["risk"]["breakeven_r"] = 1
    broker, state = start(lcfg)
    cycle_with(lcfg, state, broker, {"XBTEUR": sig("long", 0.9), "PAXGEUR": sig("hold")}, NOW,
               monkeypatch=monkeypatch)
    pos = state["positions"]["XBTEUR"]
    one_r = pos["entry_mid"] + (pos["entry_mid"] - pos["initial_stop"])
    monkeypatch.setattr(cycle.guard, "fetch_candles", lambda *a: ([{"t": int(NOW.timestamp()) + 60, "o": 55000,
                                                                   "h": one_r + 10, "l": 55000, "c": 56000}], 1))
    monkeypatch.setattr(cycle.market, "fetch_price", lambda pair: {"XBTEUR": 56000.0, "PAXGEUR": 3300.0, "EURUSD": 1.09}[pair])
    cycle.refresh(lcfg, state, now=NOW + timedelta(minutes=10), broker=broker, mode="live")
    assert kraken.orders[pos["stop_order"]]["stopPrice"] == round(60002 * 1.001)  # entry + both fees
    assert state["positions"]["XBTEUR"]["breakeven"]

    # the model's tighter stop the next day is carried over to the order too (never loosened)
    kraken.price["PF_XBTUSD"] = 64000.0
    prices = {"XBTEUR": 58000.0, "PAXGEUR": 3300.0}
    cycle_with(lcfg, state, broker, {"XBTEUR": sig("hold", stop=57000), "PAXGEUR": sig("hold")},
               NOW + timedelta(days=1), prices, monkeypatch)
    assert kraken.orders[pos["stop_order"]]["stopPrice"] == round(57000 * 64000 / 58000)


def test_model_close_and_a_failed_close(lcfg, kraken, monkeypatch, no_data):
    broker, state = start(lcfg)
    cycle_with(lcfg, state, broker, {"XBTEUR": sig("short", 0.9), "PAXGEUR": sig("long", 0.9)}, NOW,
               monkeypatch=monkeypatch)
    kraken.fail.add("mkt")
    cycle_with(lcfg, state, broker, {"XBTEUR": sig("long", 0.9), "PAXGEUR": sig("hold")}, NOW + timedelta(days=1),
               monkeypatch=monkeypatch)
    call = journal.read_calls()[-2]
    assert call["executed"] == "error" and "the close failed" in call["rule"]  # no long opened over a short
    assert state["positions"]["XBTEUR"]["side"] == "short" and kraken.positions["PF_XBTUSD"]["side"] == "short"
    assert state["positions"]["XBTEUR"]["stop_order"] in kraken.orders  # its stop still protects it
    kraken.fail.clear()
    cycle_with(lcfg, state, broker, {"XBTEUR": sig("flat", 0.9), "PAXGEUR": sig("flat", 0.9)},
               NOW + timedelta(days=2), monkeypatch=monkeypatch)
    assert kraken.positions == {} and kraken.orders == {} and state["positions"] == {}
    assert [r["reason"] for r in journal.read_trades()] == ["model: flat", "model: flat"]


def test_transfer_into_the_wallet_moves_the_reference(lcfg, kraken, monkeypatch, no_data):
    broker, state = start(lcfg)
    cycle_with(lcfg, state, broker, {"XBTEUR": sig("hold"), "PAXGEUR": sig("hold")}, NOW, monkeypatch=monkeypatch)
    kraken.wallet += 545.0
    kraken.log.append({"date": iso(NOW.timestamp() + 1800), "asset": "usd", "info": "cross-exchange transfer",
                       "old_balance": 1090.0, "new_balance": 1635.0})
    monkeypatch.setattr(cycle.market, "fetch_price", lambda pair: {**SPOT, "EURUSD": 1.09}[pair])
    cycle.refresh(lcfg, state, now=NOW + timedelta(hours=1), broker=broker, mode="live")
    assert state["start_eur"] == pytest.approx(1500.0)
    assert account.equity(lcfg, state, {**SPOT, "EURUSD": 1.09}) == pytest.approx(1500.0)  # no fake profit


def test_unknown_position_on_kraken_is_left_alone(lcfg, kraken, monkeypatch, no_data):
    broker, state = start(lcfg)
    kraken.positions["PF_ETHUSD"] = {"side": "long", "size": 1.0, "price": 2000.0}
    kraken.price["PF_ETHUSD"], kraken.half["PF_ETHUSD"] = 2000.0, 0.1
    cycle_with(lcfg, state, broker, {"XBTEUR": sig("hold"), "PAXGEUR": sig("hold")}, NOW, monkeypatch=monkeypatch)
    assert "PF_ETHUSD" in state["problems"]["live_unknown_PF_ETHUSD"]
    assert kraken.positions["PF_ETHUSD"]["size"] == 1.0


def test_watch_keeps_live_positions_after_switching_back(lcfg, kraken, monkeypatch, no_data):
    broker, state = start(lcfg)
    cycle_with(lcfg, state, broker, {"XBTEUR": sig("long", 0.9), "PAXGEUR": sig("hold")}, NOW,
               monkeypatch=monkeypatch)
    stop_usd = state["positions"]["XBTEUR"]["stop_usd"]
    # back to paper / shadow: the live state stays in its file, orders are limited to reducing ones
    monkeypatch.setattr(futures, "ALLOW", "none")
    kraken.move("PF_XBTUSD", stop_usd - 10)
    rows = live.watch(lcfg, NOW + timedelta(hours=3))
    assert rows[0]["reason"] == "STOP" and futures.ALLOW == "none"
    assert json.loads(settings.LIVE_STATE_FILE.read_text())["positions"] == {}
    assert "STOP" in settings.LIVE_TRADES_FILE.read_text()


def test_smoke_test_round_trip_leaves_nothing_behind(kraken):
    lines = []
    assert live.smoke_test(log=lines.append)
    assert kraken.positions == {} and kraken.orders == {} and futures.ALLOW == "none"
    kinds = [(n, d.get("orderType")) for n, d in kraken.calls if n in ("sendorder", "editorder", "cancelorder")]
    assert kinds[:4] == [("sendorder", "mkt"), ("sendorder", "stp"), ("sendorder", "take_profit"), ("editorder", None)]
    assert lines[-1] == "PASSED"


def test_smoke_test_failure_cleans_up(kraken):
    kraken.fail.add("take_profit")
    lines = []
    assert not live.smoke_test(log=lines.append)
    assert kraken.positions == {} and kraken.orders == {}
    assert "do not switch live on" in lines[-1]
