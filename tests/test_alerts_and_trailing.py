"""Alerts to the phone (alerts.py) and the trailing stop (risk.trail_stop)."""
import json
import sys
from datetime import datetime, timedelta

import pytest

from krakenbot import alerts, cli, exchange_stops, homeassistant, kraken, market, risk
from conftest import BASE_CFG, make_state

NOW = datetime(2026, 11, 7, 12, 0)


@pytest.fixture
def pushed(monkeypatch):
    """Captures notifications instead of calling Home Assistant."""
    sent = []
    monkeypatch.setattr(homeassistant, "ha_conf", lambda cfg: {"url": "http://ha", "token": "x",
                                                               "notify": "notify.mobile_app_phone"})
    monkeypatch.setattr(homeassistant, "ha_post", lambda ha, path, body: sent.append((path, body)))
    return sent


# ---------------------------------------------------------------- alerts
def test_send_goes_to_the_notify_service(cfg, pushed):
    assert alerts.send(cfg, "Title", "Body")
    assert pushed == [("services/notify/mobile_app_phone", {"title": "Title", "message": "Body"})]


def test_send_without_notify_only_prints(cfg, monkeypatch, capsys):
    monkeypatch.setattr(homeassistant, "ha_conf", lambda cfg: None)
    assert not alerts.send(cfg, "Title", "Body")
    assert "[alert] Title: Body" in capsys.readouterr().out


def test_send_never_raises(cfg, monkeypatch):
    monkeypatch.setattr(homeassistant, "ha_conf", lambda cfg: {"notify": "notify.x"})
    monkeypatch.setattr(homeassistant, "ha_post", lambda *a: (_ for _ in ()).throw(RuntimeError("HA down")))
    assert not alerts.send(cfg, "Title", "Body")


def test_problem_is_sent_once_repeated_later_and_resolved(cfg, pushed):
    state = {}
    alerts.problem(state, "exchange_stop", "stop order failed")
    assert alerts.flush(cfg, state, NOW) and len(pushed) == 1
    assert not alerts.flush(cfg, state, NOW + timedelta(hours=5)) and len(pushed) == 1  # no spam
    assert alerts.flush(cfg, state, NOW + timedelta(hours=6)) and len(pushed) == 2  # still broken: reminder
    alerts.problem(state, "exchange_stop")
    assert alerts.flush(cfg, state, NOW + timedelta(hours=7))
    assert pushed[-1][1] == {"title": "Kraken bot: resolved", "message": "Works again: stop order failed"}
    assert state["alerts_sent"] == {} and not alerts.flush(cfg, state, NOW + timedelta(hours=8))


def test_run_watch_alerts_on_failed_cycle_and_refreshes(cfg, pushed):
    watch = alerts.RunWatch(lambda: cfg)
    watch.cycle(0, "")
    watch.cycle(1, "RuntimeError: Claude did not return a full answer")
    assert "exit code 1" in pushed[-1][1]["message"] and "Claude" in pushed[-1][1]["message"]
    pushed.clear()
    for _ in range(2):
        watch.refresh(1, "ConnectionError")
    assert pushed == []  # one or two failures happen
    watch.refresh(1, "ConnectionError")
    assert "3 times in a row" in pushed[-1][1]["message"]
    watch.refresh(1, "ConnectionError")
    assert len(pushed) == 1  # not every time
    watch.refresh(0, "")
    assert pushed[-1][1]["message"] == "Refresh works again."
    watch.refresh(0, "")
    assert len(pushed) == 2


def test_run_watch_survives_a_broken_config(pushed):
    watch = alerts.RunWatch(lambda: json.loads("not json"))
    watch.cycle(2, "boom")
    assert len(pushed) == 1


def test_exchange_stop_failure_becomes_a_problem(cfg, monkeypatch):
    cfg["live_validate_only"] = False
    monkeypatch.setattr(kraken, "kraken_private", lambda *a: (_ for _ in ()).throw(RuntimeError("EAPI:Invalid key")))
    state = make_state()
    exchange_stops.sync(cfg, state, {"XBTEUR": {"key": "XXBTZEUR", "lot_decimals": 8, "ordermin": 0.0001}},
                        {"XBTEUR": 70000.0})
    assert "EAPI:Invalid key" in state["problems"]["exchange_stop"]
    monkeypatch.setattr(kraken, "kraken_private", lambda path, data: {"open": {}})
    exchange_stops.sync(cfg, state, {"XBTEUR": {"key": "XXBTZEUR", "lot_decimals": 8, "ordermin": 0.0001}},
                        {"XBTEUR": 70000.0})
    assert state["problems"] == {}


# ---------------------------------------------------------------- trailing stop
def held(**kw):
    return {"coin": 0.01, "entry_price": 60000.0, "stop_price": 57000.0, **kw}


def test_trailing_stop_follows_the_peak_up_only(cfg):
    pos = held()
    assert risk.trail_stop(cfg, pos, 60000.0, 1000.0) == 58000.0  # 2 ATR below the first peak
    assert risk.trail_stop(cfg, pos, 61000.0, 1000.0) == 59000.0
    assert risk.trail_stop(cfg, pos, 60500.0, 1000.0) is None  # price falls: the stop stays
    assert pos["stop_price"] == 59000.0 and pos["peak_price"] == 61000.0
    assert risk.trail_stop(cfg, pos, 61050.0, 1000.0) is None  # less than 0.1 ATR higher: no churn
    assert risk.trail_stop(cfg, pos, 61100.0, 1000.0) == 59100.0


def test_trailing_never_lowers_a_higher_model_stop(cfg):
    pos = held(stop_price=59500.0)
    assert risk.trail_stop(cfg, pos, 61000.0, 1000.0) is None and pos["stop_price"] == 59500.0


def test_trailing_off_without_atr_position_or_when_disabled(cfg):
    assert risk.trail_stop(cfg, held(), 61000.0, None) is None
    assert risk.trail_stop(cfg, held(coin=0.0001), 61000.0, 1000.0) is None  # 6 EUR: dust
    cfg["risk"]["trailing_stop_atr"] = 0
    assert risk.trail_stop(cfg, held(), 61000.0, 1000.0) is None
    cfg["risk"]["trailing_stop_atr"] = 1.5
    assert risk.trail_stop(cfg, held(), 61000.0, 1000.0) == 59500.0


def test_peak_is_set_on_buy_and_cleared_on_sale(cfg):
    from krakenbot import portfolio
    state = make_state()
    portfolio.paper_execute(cfg, state, "XBTEUR", {"side": "buy", "eur": 100.0}, 100.0)
    pos = state["positions"]["XBTEUR"]
    assert pos["peak_price"] == pytest.approx(100.1)  # the fill with slippage
    portfolio.paper_execute(cfg, state, "XBTEUR", {"side": "sell", "coin": pos["coin"]}, 120.0)
    assert pos["peak_price"] is None


def test_refresh_raises_the_stop_and_saves_it(isolated, monkeypatch):
    (isolated / "config.json").write_text(json.dumps(BASE_CFG), encoding="utf-8")
    state = make_state(eur=0, XBTEUR={"coin": 0.01, "entry_price": 60000.0, "stop_price": 57000.0})
    state["last_cycle"] = {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "prices": {}, "atr": {"XBTEUR": 1000.0}}
    (isolated / "state.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(market, "fetch_price", lambda pair: {"XBTEUR": 62000.0, "ETHEUR": 2000.0, "SOLEUR": 80.0}[pair])
    monkeypatch.setattr(sys, "argv", ["bot.py", "--ha"])
    cli.main()
    saved = json.loads((isolated / "state.json").read_text(encoding="utf-8"))["positions"]["XBTEUR"]
    assert saved["stop_price"] == 60000.0 and saved["peak_price"] == 62000.0 and saved["coin"] == 0.01


def test_failed_extra_cycle_alerts_and_does_not_crash_the_refresh(isolated, monkeypatch, pushed):
    (isolated / "config.json").write_text(json.dumps(BASE_CFG), encoding="utf-8")
    state = make_state()
    state["last_cycle"] = {"date": "2000-01-01 08:05", "prices": {"XBTEUR": 100.0}, "atr": {"XBTEUR": 1.0}}
    (isolated / "state.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(market, "fetch_price", lambda pair: 200.0)  # a big move: an extra cycle
    monkeypatch.setattr(cli, "daily_cycle", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Claude overloaded")))
    monkeypatch.setattr(homeassistant, "ha_publish", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["bot.py", "--ha"])
    cli.main()  # does not raise
    assert "Claude overloaded" in pushed[-1][1]["message"]
    saved = json.loads((isolated / "state.json").read_text(encoding="utf-8"))
    assert saved["extra_cycles"]["n"] == 1  # counted: the next refresh does not retry at once
