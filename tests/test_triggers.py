"""Extra cycles between the daily ones (triggers.py) and the hourly candles the model gets."""
import json
import sys
from datetime import datetime, timedelta

import pytest

from krakenbot import claude, cli, indicators, journal, market, triggers
from conftest import BASE_CFG, log_row, make_candles, make_state, signal

NOW = datetime(2026, 11, 5, 20, 0)
PRICES = {"XBTEUR": 100.0, "ETHEUR": 2000.0, "SOLEUR": 80.0}


def at(hours_ago, now=NOW):
    return (now - timedelta(hours=hours_ago)).strftime(triggers.FMT)


def cycled(hours_ago, prices=None, atr=None, now=NOW):
    state = make_state()
    state["last_cycle"] = {"date": at(hours_ago, now), "prices": dict(prices or PRICES),
                           "atr": atr or {"XBTEUR": 2.0, "ETHEUR": 40.0, "SOLEUR": 2.0}}
    return state


def stop_row(hours_ago, pair="XBTEUR", now=NOW):
    return log_row(at(hours_ago, now), pair=pair, price=98.0, action="stop", executed="sell",
                   rule="STOP (level 98)")


def test_nothing_before_the_first_cycle_on_this_version(cfg):
    assert triggers.extra_cycle_reason(cfg, make_state(), PRICES, [stop_row(5)], NOW) is None


def test_stop_triggers_after_the_waiting_time(cfg):
    state = cycled(12)
    assert triggers.extra_cycle_reason(cfg, state, PRICES, [stop_row(2)], NOW) is None  # too early
    reason = triggers.extra_cycle_reason(cfg, state, PRICES, [stop_row(3)], NOW)
    assert reason["kind"] == "stop" and reason["pair"] == "XBTEUR" and reason["rule"] == "STOP (level 98)"
    assert reason["price_now"] == 100.0


def test_stop_before_the_last_cycle_is_already_handled(cfg):
    assert triggers.extra_cycle_reason(cfg, cycled(4), PRICES, [stop_row(5)], NOW) is None


def test_large_move_since_the_last_cycle(cfg):
    state = cycled(4)
    small = {**PRICES, "XBTEUR": 97.5}  # 1.25 ATR
    assert triggers.extra_cycle_reason(cfg, state, small, [], NOW) is None
    reason = triggers.extra_cycle_reason(cfg, state, {**PRICES, "XBTEUR": 96.9}, [], NOW)  # 1.55 ATR
    assert reason["kind"] == "move" and reason["pair"] == "XBTEUR"
    assert reason["atr_multiple"] == pytest.approx(1.55) and reason["change_pct"] == pytest.approx(-3.1)


def test_a_move_up_counts_too(cfg):
    reason = triggers.extra_cycle_reason(cfg, cycled(4), {**PRICES, "ETHEUR": 2061.0}, [], NOW)
    assert reason["pair"] == "ETHEUR" and reason["change_pct"] > 0


def test_minimum_gap_since_any_cycle(cfg):
    moved = {**PRICES, "XBTEUR": 90.0}
    assert triggers.extra_cycle_reason(cfg, cycled(2.9), moved, [], NOW) is None
    assert triggers.extra_cycle_reason(cfg, cycled(3), moved, [], NOW)


def test_daily_limit_resets_the_next_day(cfg):
    state, moved = cycled(4), {**PRICES, "XBTEUR": 90.0}
    for _ in range(3):
        triggers.count_extra(state, NOW)
    assert state["extra_cycles"] == {"date": "2026-11-05", "n": 3}
    assert triggers.extra_cycle_reason(cfg, state, moved, [], NOW) is None
    tomorrow = NOW + timedelta(days=1)
    assert triggers.extra_cycle_reason(cfg, state, moved, [], tomorrow)
    triggers.count_extra(state, tomorrow)
    assert state["extra_cycles"] == {"date": "2026-11-06", "n": 1}


def test_settings_can_switch_it_off_or_change_thresholds(cfg):
    moved = {**PRICES, "XBTEUR": 97.5}  # 1.25 ATR
    cfg["extra_cycles"] = {"enabled": False}
    assert triggers.extra_cycle_reason(cfg, cycled(4), {**PRICES, "XBTEUR": 50.0}, [], NOW) is None
    cfg["extra_cycles"] = {"move_atr": 1.0}
    assert triggers.extra_cycle_reason(cfg, cycled(4), moved, [], NOW)["kind"] == "move"


def test_remember_cycle_and_cycle_info():
    state = make_state()
    markets = {pair: {"atr14": 1.5} for pair in PRICES}
    triggers.remember_cycle(state, markets, PRICES, NOW)
    assert state["last_cycle"] == {"date": "2026-11-05 20:00", "prices": PRICES,
                                   "atr": {pair: 1.5 for pair in PRICES}, "watch": {}}
    assert triggers.cycle_info(None) == {"type": "scheduled_daily"}
    assert triggers.cycle_info({"kind": "move"}) == {"type": "extra", "trigger": {"kind": "move"}}


def test_describe_both_kinds():
    assert "STOP (level 98)" in triggers.describe({"kind": "stop", "pair": "XBTEUR", "rule": "STOP (level 98)",
                                                   "at": "2026-11-05 16:55"})
    text = triggers.describe({"kind": "move", "pair": "XBTEUR", "since": "2026-11-05 08:05", "price_then": 100.0,
                              "price_now": 96.9, "change_pct": -3.1, "atr_multiple": 1.55})
    assert "-3.10%" in text and "1.55 ATR" in text


# ---------------------------------------------------------------- hourly candles
def hourly_candles(closes, volume=10.0):
    return [{"t": 1793800800 + i * 3600, "o": c - 1, "h": c + 2, "l": c - 2, "c": c, "v": volume}
            for i, c in enumerate(closes)]


def test_hourly_summary():
    out = indicators.hourly(hourly_candles([100 + i for i in range(24)]), 125.0)
    assert len(out["rows"]) == 24 and out["columns"][0] == "hour_utc"
    assert out["rows"][0][1:] == [99.0, 100.0, 102.0, 98.0, 1.0]  # open, close, high, low, volume_rel
    assert out["high"] == 125.0 and out["low"] == 98.0
    assert out["change_pct"] == pytest.approx(26.26, abs=0.01)
    assert indicators.hourly([], 1.0) is None


def test_recent_decisions_count_days_not_cycles(isolated):
    rows = [log_row(f"2026-11-{d:02d} {h}", price=100.0) for d in range(1, 9) for h in ("08:05", "14:00", "20:00")]
    journal.log_rows(rows)
    out = claude.recent_decisions(["XBTEUR"], {"XBTEUR": 100.0})["XBTEUR"]
    days = {r["date"][:10] for r in out}
    assert len(out) == 21 and min(days) == "2026-11-02"


# ---------------------------------------------------------------- refresh starts the extra cycle
def test_refresh_runs_an_extra_cycle_after_a_stop(isolated, monkeypatch):
    (isolated / "config.json").write_text(json.dumps(BASE_CFG), encoding="utf-8")
    now = datetime.now()
    state = cycled(6, now=now)
    (isolated / "state.json").write_text(json.dumps(state), encoding="utf-8")
    journal.log_rows([stop_row(4, now=now)])

    daily = make_candles([100.0 + (i % 5) for i in range(60)])
    monkeypatch.setattr(market, "fetch_price", lambda pair: PRICES[pair])
    monkeypatch.setattr(market, "fetch_ohlc", lambda pair, days=60, interval=1440: (daily, PRICES[pair]))
    monkeypatch.setattr(market, "fetch_hourly", lambda pair: hourly_candles([100.0] * 24))
    asked = []

    def fake_claude(cfg, markets, news, portfolio, context):
        asked.append((markets, context))
        return {pair: signal() for pair in cfg["pairs"]}, "test"

    monkeypatch.setattr(claude, "ask_claude", fake_claude)
    monkeypatch.setattr(sys, "argv", ["bot.py", "--ha"])
    cli.main()

    assert len(asked) == 1
    markets, context = asked[0]
    assert context["this_cycle"]["type"] == "extra" and context["this_cycle"]["trigger"]["kind"] == "stop"
    assert len(markets["XBTEUR"]["last_24_hours"]["rows"]) == 24
    saved = json.loads((isolated / "state.json").read_text(encoding="utf-8"))
    assert saved["extra_cycles"]["n"] == 1 and saved["last_cycle"]["date"] >= now.strftime(triggers.FMT)
    assert sum(1 for r in journal.read_log() if r["model_action"] == "hold") == 3

    asked.clear()  # the stop is handled now: the next refresh does not ask again
    cli.main()
    assert asked == []
