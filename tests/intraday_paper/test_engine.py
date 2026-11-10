"""The hourly step: entries, what blocks them, the exits."""
from datetime import datetime, timedelta

import pytest
from .intraday_helpers import NOW, breakout_up, hours, quiet, set_regime

from intraday import engine


def run(cfg, state, cs=None, price=101.5, book="model", now=NOW, pair="XBTEUR"):
    cs = breakout_up() if cs is None else cs
    return engine.step(cfg, state, book, {pair: cs}, {pair: price}, now)


def test_a_signal_opens_one_position_per_pair(icfg, istate):
    set_regime(istate, "trend_up")
    rows, events, status = run(icfg, istate)
    pos = istate["books"]["model"]["positions"]["XBTEUR"]
    assert rows == [] and events[0]["kind"] == "open" and events[0]["strategy"] == "breakout"
    assert pos["strategy"] == "breakout" and pos["regime"] == "trend_up" and pos["risk_dist"] > 0
    assert istate["books"]["model"]["day"]["trades"] == {"XBTEUR": 1}
    assert status["XBTEUR"].startswith("opened")
    # 0.5 % of equity at risk, capped by 25 % margin at leverage 2
    assert pos["qty"] * 101.5 == pytest.approx(500, rel=0.01)
    _, events, status = run(icfg, istate)
    assert events == [] and status["XBTEUR"].startswith("holding")
    assert istate["books"]["rules"]["positions"] == {}  # the other book has no regime yet


def test_regime_blocks(icfg, istate):
    _, _, status = run(icfg, istate)
    assert status["XBTEUR"] == "no regime yet"
    set_regime(istate, "trend_up", age_hours=31)
    assert "out of date" in run(icfg, istate)[2]["XBTEUR"]
    set_regime(istate, "trend_up", confidence=0.4)
    assert "confidence 0.40 < 0.5" in run(icfg, istate)[2]["XBTEUR"]
    set_regime(istate, "no_trade")
    assert run(icfg, istate)[2]["XBTEUR"] == "regime no_trade"
    set_regime(istate, "trend_up", book="rules", confidence=None)  # the rule has no confidence
    assert run(icfg, istate, book="rules")[2]["XBTEUR"].startswith("opened")


def test_daily_loss_and_trade_count_limits(icfg, istate):
    set_regime(istate, "trend_up")
    book = istate["books"]["model"]
    engine.roll_day(icfg, book, {}, NOW)
    book["day"]["trades"]["XBTEUR"] = 4
    assert run(icfg, istate)[2]["XBTEUR"] == "4 trades today already"
    book["day"]["trades"] = {}
    book["balance"] = 975.0  # -2.5 % since the start of the day
    assert "daily loss 2.5%" in run(icfg, istate)[2]["XBTEUR"]
    later = NOW + timedelta(days=1)  # a new day starts from the current equity
    set_regime(istate, "trend_up", now=later)
    status = run(icfg, istate, cs=hours([c["c"] for c in breakout_up()], now=later), now=later)[2]
    assert status["XBTEUR"].startswith("opened") and book["day"]["start_equity"] == 975.0


def test_blackout_around_a_high_impact_release(icfg, istate):
    set_regime(istate, "trend_up")
    release = (NOW + timedelta(minutes=40)).astimezone()
    istate["macro_calendar_cache"] = {"events": [{"country": "USD", "impact": "High", "title": "CPI m/m",
                                                  "date": release.isoformat()}]}
    assert run(icfg, istate)[2]["XBTEUR"].startswith("blackout: CPI m/m")
    assert run(icfg, istate, now=NOW + timedelta(hours=2), cs=hours([c["c"] for c in breakout_up()],
               now=NOW + timedelta(hours=2)))[2]["XBTEUR"].startswith("opened")  # an hour after it


def test_no_recent_candle_no_entry(icfg, istate):
    set_regime(istate, "trend_up")
    old = hours([c["c"] for c in breakout_up()], now=NOW - timedelta(hours=5))
    assert run(icfg, istate, cs=old)[2]["XBTEUR"] == "no recent hourly candle"


def test_time_exit(icfg, istate):
    set_regime(istate, "trend_up")
    run(icfg, istate)
    later = NOW + timedelta(hours=24)
    set_regime(istate, "trend_up", now=later)
    rows, _, status = run(icfg, istate, cs=hours(quiet(), now=later), price=101.0, now=later)
    assert rows[0]["reason"] == "TIME" and rows[0]["book"] == "model" and rows[0]["strategy"] == "breakout"
    assert rows[0]["hours"] == 24.0 and status["XBTEUR"].startswith("closed breakout long (TIME)")


def test_regime_change_closes_what_it_no_longer_allows(icfg, istate):
    set_regime(istate, "trend_up")
    run(icfg, istate)
    set_regime(istate, "range")
    rows, _, _ = run(icfg, istate, cs=hours(quiet() + [101.5, 101.6]), price=101.6)
    assert rows[0]["reason"] == "REGIME" and rows[0]["regime"] == "trend_up"


def test_an_out_of_date_regime_does_not_close_a_position(icfg, istate):
    set_regime(istate, "trend_up")
    run(icfg, istate)
    set_regime(istate, "no_trade", age_hours=40)
    rows, _, status = run(icfg, istate, cs=hours(quiet() + [101.5, 101.6]), price=101.6)
    assert rows == [] and status["XBTEUR"].startswith("holding")


def test_gold_is_closed_before_the_weekend_and_not_traded_over_it(icfg):
    icfg["pairs"] = ["PAXGEUR"]
    friday = datetime(2026, 11, 6, 22, 3)  # gold closes at 23:00
    state = engine.new_state(icfg, friday - timedelta(days=1))
    set_regime(state, "trend_up", pair="PAXGEUR", now=friday - timedelta(hours=5))
    thursday = friday - timedelta(hours=4)
    run(icfg, state, cs=hours(quiet(level=4000, amp=8) + [4060], now=thursday, wick=4), price=4060, now=thursday,
        pair="PAXGEUR")
    assert "PAXGEUR" in state["books"]["model"]["positions"]
    rows, _, _ = run(icfg, state, cs=hours(quiet(level=4000, amp=8) + [4060, 4065], now=friday, wick=4),
                     price=4065, now=friday, pair="PAXGEUR")
    assert rows[0]["reason"] == "WEEKEND"
    saturday = friday + timedelta(hours=12)
    status = run(icfg, state, cs=hours(quiet(level=4000, amp=8) + [4060], now=saturday, wick=4), price=4060,
                 now=saturday, pair="PAXGEUR")[2]
    assert status["PAXGEUR"] == "market closed"


def test_snapshot_keeps_the_days_equity(icfg, istate):
    engine.snapshot(icfg, istate, {}, NOW)
    engine.snapshot(icfg, istate, {}, NOW + timedelta(hours=3))
    assert istate["books"]["model"]["equity_by_day"] == {"2026-11-04": 1000.0}
