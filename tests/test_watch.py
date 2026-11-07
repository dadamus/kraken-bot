"""The model's entry condition (watch): what would change its call, and the extra cycle at its price levels."""
import json
import sys
from datetime import datetime, timedelta

import pytest

from krakenbot import claude, cli, journal, market, panel, triggers
from conftest import BASE_CFG, log_row, make_candles, make_state, signal

NOW = datetime(2026, 11, 7, 12, 0)
PRICES = {"XBTEUR": 68000.0, "ETHEUR": 2000.0, "SOLEUR": 80.0}
ATR = {"XBTEUR": 2000.0, "ETHEUR": 80.0, "SOLEUR": 4.0}
WATCH = {"XBTEUR": {"above": 68500.0, "below": 66800.0, "condition": "buy if BTC holds above 68500"}}


def cycled(hours_ago, watch=None, now=NOW):
    state = make_state()
    state["last_cycle"] = {"date": (now - timedelta(hours=hours_ago)).strftime(triggers.FMT),
                           "prices": dict(PRICES), "atr": dict(ATR), "watch": watch if watch is not None else WATCH}
    return state


# ---------------------------------------------------------------- the model's answer
def test_parse_watch():
    w = claude.parse_signal({"action": "hold", "watch": {"price_above": "68500", "price_below": None,
                                                         "condition": "  buy above 68500  "}})["watch"]
    assert w == {"above": 68500.0, "below": None, "condition": "buy above 68500"}
    assert claude.parse_watch({"price_above": "abc", "price_below": -5, "condition": "x" * 500}) == \
        {"above": None, "below": None, "condition": "x" * 300}
    assert claude.parse_watch(None) == {"above": None, "below": None, "condition": ""}
    assert claude.parse_signal({"action": "hold"})["watch"]["condition"] == ""  # an older answer without it


def test_schema_asks_for_watch():
    item = claude.decision_schema(["XBTEUR"])["properties"]["decisions"]["items"]
    assert "watch" in item["required"]
    assert set(item["properties"]["watch"]["required"]) == {"price_above", "price_below", "condition"}
    assert "watch" in claude.SYSTEM_PROMPT and "watch.condition" in claude.SYSTEM_PROMPT


def test_levels_on_the_wrong_side_are_dropped():
    sigs = {"XBTEUR": {"watch": {"above": 67000.0, "below": 69000.0, "condition": "c"}},  # both already crossed
            "ETHEUR": {"watch": {"above": 2100.0, "below": 1900.0, "condition": ""}},
            "SOLEUR": {"watch": {"above": None, "below": None, "condition": ""}}}  # nothing to keep
    assert triggers.watch_levels(sigs, PRICES) == {
        "XBTEUR": {"above": None, "below": None, "condition": "c"},
        "ETHEUR": {"above": 2100.0, "below": 1900.0, "condition": ""}}
    assert triggers.watch_levels({"XBTEUR": signal()}, PRICES) == {}  # a signal without watch


# ---------------------------------------------------------------- the extra cycle
def test_crossing_a_level_starts_an_extra_cycle(cfg):
    state = cycled(4)
    assert triggers.extra_cycle_reason(cfg, state, {**PRICES, "XBTEUR": 68400.0}, [], NOW) is None
    reason = triggers.extra_cycle_reason(cfg, state, {**PRICES, "XBTEUR": 68600.0}, [], NOW)
    assert reason == {"kind": "watch", "pair": "XBTEUR", "direction": "above", "level": 68500.0,
                      "condition": "buy if BTC holds above 68500", "since": "2026-11-07 08:00",
                      "price_then": 68000.0, "price_now": 68600.0}
    down = triggers.extra_cycle_reason(cfg, state, {**PRICES, "XBTEUR": 66700.0}, [], NOW)
    assert down["direction"] == "below" and down["level"] == 66800.0
    assert "66800" in triggers.describe(down) and "buy if BTC" in triggers.describe(reason)


def test_watch_keeps_the_limits_and_can_be_switched_off(cfg):
    above = {**PRICES, "XBTEUR": 68600.0}
    assert triggers.extra_cycle_reason(cfg, cycled(2), above, [], NOW) is None  # min_gap_hours
    state = cycled(4)
    for _ in range(3):
        triggers.count_extra(state, NOW)
    assert triggers.extra_cycle_reason(cfg, state, above, [], NOW) is None  # max_per_day
    cfg["extra_cycles"] = {"watch_levels": False}
    assert triggers.extra_cycle_reason(cfg, cycled(4), above, [], NOW) is None
    assert triggers.extra_cycle_reason(cfg, cycled(4, watch={}), above, [], NOW) is None  # older state


def test_a_stop_comes_first_and_a_level_before_a_large_move(cfg):
    state = cycled(4, watch={"ETHEUR": {"above": 2050.0, "below": None, "condition": "c"}})
    moved = {**PRICES, "XBTEUR": 71500.0, "ETHEUR": 2060.0}  # BTC +1.75 ATR, ETH above its level
    assert triggers.extra_cycle_reason(cfg, state, moved, [], NOW)["kind"] == "watch"
    stop = log_row("2026-11-07 08:30", pair="SOLEUR", action="stop", executed="sell", rule="STOP (level 78)")
    assert triggers.extra_cycle_reason(cfg, state, moved, [stop], NOW)["kind"] == "stop"


def test_daily_cycle_keeps_the_watch_and_logs_the_condition(isolated, monkeypatch):
    (isolated / "config.json").write_text(json.dumps(BASE_CFG), encoding="utf-8")
    (isolated / "state.json").write_text(json.dumps(make_state()), encoding="utf-8")
    daily = make_candles([100.0 + (i % 5) for i in range(60)])
    monkeypatch.setattr(market, "fetch_ohlc", lambda pair, days=60, interval=1440: (daily, 100.0))
    monkeypatch.setattr(market, "fetch_hourly", lambda pair: None)
    monkeypatch.setattr(market, "fetch_news", lambda feeds: [])
    for name in ("fetch_derivatives", "fetch_fear_greed", "fetch_market_overview", "fetch_implied_vol"):
        monkeypatch.setattr(market, name, lambda *a: None)
    monkeypatch.setattr(cli.macro, "fetch_us_markets", lambda: None)
    monkeypatch.setattr(cli.macro, "fetch_macro_calendar", lambda state: None)
    monkeypatch.setattr(cli.macro, "fetch_coinbase_premium", lambda: None)

    def fake_claude(cfg, markets, news, portfolio, context):
        sigs = {pair: dict(signal(), watch=claude.parse_watch(None)) for pair in cfg["pairs"]}
        sigs["XBTEUR"]["watch"] = {"above": 105.0, "below": 95.0, "condition": "buy above 105"}
        return sigs, "test"

    monkeypatch.setattr(claude, "ask_claude", fake_claude)
    monkeypatch.setattr(sys, "argv", ["bot.py"])
    cli.main()
    saved = json.loads((isolated / "state.json").read_text(encoding="utf-8"))
    assert saved["last_cycle"]["watch"] == {"XBTEUR": {"above": 105.0, "below": 95.0, "condition": "buy above 105"}}
    rows = {r["pair"]: r for r in journal.read_log()}
    assert rows["XBTEUR"]["watch"] == "buy above 105" and rows["ETHEUR"]["watch"] == ""
    recent = claude.recent_decisions(["XBTEUR", "ETHEUR"], {"XBTEUR": 100.0, "ETHEUR": 100.0})
    assert recent["XBTEUR"][0]["watch"] == "buy above 105" and "watch" not in recent["ETHEUR"][0]


# ---------------------------------------------------------------- the panel
def test_panel_band_uses_the_closer_level(cfg):
    state = cycled(4, watch={"XBTEUR": {"above": 68500.0, "below": 60000.0, "condition": "c"}})
    pair = panel.thresholds(cfg, state, PRICES, NOW)["pairs"]["XBTEUR"]
    assert (pair["low"], pair["low_from"]) == (65000.0, "atr")  # 60000 is further than 1.5 ATR
    assert (pair["high"], pair["high_from"]) == (68500.0, "watch")
    assert pair["watch"]["condition"] == "c"
    cfg["extra_cycles"] = {"watch_levels": False}
    assert panel.thresholds(cfg, state, PRICES, NOW)["pairs"]["XBTEUR"]["high"] == 71000.0
