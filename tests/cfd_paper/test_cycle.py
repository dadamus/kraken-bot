"""The daily cycle end to end (model and data replaced), the refresh and the panel."""
from datetime import timedelta

import pytest
from .cfd_helpers import NOW, sig

from cfd import account, cycle, journal, panel


def fake_markets(prices):
    def load(pairs, offline_mode, crypto=None):
        markets = {p: {"atr14": prices[p] * 0.03, "price_now": prices[p]} for p in pairs}
        candles = {p: [] for p in pairs}
        return markets, dict(prices), candles
    return load


@pytest.fixture
def no_data(monkeypatch):
    monkeypatch.setattr(cycle.market, "fetch_news", lambda feeds: ["headline"])
    monkeypatch.setattr(cycle, "load_context", lambda pairs, state, closed: {"fear_greed": {"today": 50}})


def test_daily_cycle_opens_closes_and_logs(ccfg, cstate, monkeypatch, no_data):
    prices = {"XBTEUR": 50000.0, "ETHEUR": 3000.0, "SOLEUR": 150.0}
    monkeypatch.setattr(cycle, "load_markets", fake_markets(prices))
    seen = {}

    def ask(cfg, payload):
        seen.update(payload)
        return {"XBTEUR": sig("short", 0.8), "ETHEUR": sig("long", 0.5), "SOLEUR": sig("long", 0.9)}, "s"

    cycle.daily_cycle(ccfg, cstate, now=NOW, ask=ask)
    assert {p: v["side"] for p, v in cstate["positions"].items()} == {"XBTEUR": "short", "SOLEUR": "long"}
    assert seen["trading_terms"]["instruments"]["XBTEUR"]["leverage"] == 2 and seen["news_last_24h"] == ["headline"]
    assert seen["fear_greed"] == {"today": 50}
    calls = journal.read_calls()
    assert [r["executed"] for r in calls] == ["open_short", "none", "open_long"]
    assert account.load_state(ccfg)["first_prices"]["XBTEUR"] == 50000.0

    # the next day: BTC fell, the model wants out; SOL reverses to a short
    prices2 = {"XBTEUR": 48000.0, "ETHEUR": 3000.0, "SOLEUR": 151.0}
    monkeypatch.setattr(cycle, "load_markets", fake_markets(prices2))
    monkeypatch.setattr(cycle.guard, "fetch_candles", lambda *a: ([], 1))
    cycle.daily_cycle(ccfg, cstate, now=NOW + timedelta(days=1), ask=lambda c, p: (
        {"XBTEUR": sig("flat", 0.5), "ETHEUR": sig("hold"), "SOLEUR": sig("short", 0.8)}, "s2"))
    trades = journal.read_trades()
    assert [(r["pair"], r["side"]) for r in trades] == [("XBTEUR", "short"), ("SOLEUR", "long")]
    assert float(trades[0]["net_eur"]) > 0 and float(trades[0]["swap_eur"]) > 0
    assert cstate["positions"]["SOLEUR"]["side"] == "short"
    assert journal.read_calls()[-1]["executed"] == "close_long+open_short"
    assert cstate["evaluation"]["trades"]["short"]["n"] == 1


def test_offline_cycle_needs_no_network(ccfg, cstate):
    cycle.daily_cycle(ccfg, cstate, offline_mode=True, now=NOW)  # the root conftest blocks requests
    assert len(journal.read_calls()) == 3


def test_refresh_closes_on_stop_and_saves(ccfg, cstate, monkeypatch):
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 48000, 55000, NOW)
    monkeypatch.setattr(cycle.market, "fetch_price", lambda pair: {"XBTEUR": 47500.0}.get(pair, 100.0))
    monkeypatch.setattr(cycle.guard, "fetch_candles", lambda *a: ([], 1))
    cycle.refresh(ccfg, cstate, now=NOW + timedelta(hours=1))
    assert journal.read_trades()[0]["reason"] == "STOP"
    assert account.load_state(ccfg)["positions"] == {}


def test_panel_renders_positions_calls_and_trades(ccfg, cstate):
    account.open_position(ccfg, cstate, "XBTEUR", "short", 0.01, 50000, 52000, 46000, NOW)
    cstate["last_prices"] = {"XBTEUR": 49000}
    calls = [{"date": "2026-11-02 08:20", "pair": "XBTEUR", "action": "short", "confidence": "0.8",
              "p_up_3d": "0.4", "executed": "open_short", "rule": "r", "reasoning": "<b>x</b>"}]
    out = panel.render(ccfg, cstate, calls, [], {"run_time": "08:20"})
    assert "XBTEUR" in out and "open_short" in out
    assert "<b>x</b>" not in out  # escaped
