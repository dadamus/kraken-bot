"""Indicators computed from candles and strength versus BTC."""
from datetime import datetime, timezone

import pytest

import bot
from conftest import MONDAY, make_candles


def rising(n, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def test_basic_indicators_on_steady_uptrend():
    closes = rising(300)
    closed = make_candles(closes)
    price = closes[-1] + 1
    m = bot.indicators(closed, price)

    assert m["sma50"] == pytest.approx(sum(closes[-50:]) / 50)
    assert m["sma200"] == pytest.approx(sum(closes[-200:]) / 200)
    assert m["pct_vs_sma200"] > m["pct_vs_sma50"] > 0  # in an uptrend the price is above the averages
    assert m["sma200_change_20d_pct"] > 0
    assert m["rsi14"] == 100.0  # only gains
    assert m["atr14"] == pytest.approx(2.0)  # high - low = 2 every day
    assert m["atr14_pct"] == pytest.approx(2.0 / price * 100, abs=0.01)
    assert m["change_1d_pct"] == pytest.approx((closes[-1] / closes[-2] - 1) * 100, abs=0.01)
    assert m["range_365d"]["days"] == 300
    assert m["change_365d_pct"] is None  # not enough history for a year


def test_weekly_candles_skip_incomplete_week():
    closes = rising(300)  # 42 complete weeks + 6 days
    weeks = bot.indicators(make_candles(closes), closes[-1])["last_26_weeks"]["rows"]

    assert len(weeks) == 26
    for start, *_ in weeks:
        assert datetime.strptime(start, "%Y-%m-%d").weekday() == 0  # weeks start on Monday
    last_full = datetime.fromtimestamp(MONDAY + 41 * 7 * 86400, timezone.utc).strftime("%Y-%m-%d")
    assert weeks[-1][0] == last_full
    assert weeks[-1][1] == closes[41 * 7 + 6]  # weekly close = Sunday's close


def test_short_history_gives_none_instead_of_crashing():
    closes = rising(60)
    m = bot.indicators(make_candles(closes), closes[-1])

    assert m["sma200"] is None and m["pct_vs_sma200"] is None
    assert m["sma200_change_20d_pct"] is None
    assert m["change_90d_pct"] is None
    assert m["sma50"] is not None and m["atr14"] is not None


def test_rsi_of_steady_downtrend_is_zero():
    closes = rising(60, start=200, step=-1)
    assert bot.indicators(make_candles(closes), closes[-1])["rsi14"] == 0.0


def test_relative_strength_vs_btc():
    btc = make_candles([100.0] * 60)
    eth = make_candles(rising(60, start=10))  # rising while BTC is flat
    sol = make_candles(rising(60, start=70, step=-1))  # falling
    prices = {"XBTEUR": 100.0, "ETHEUR": 70.0, "SOLEUR": 10.0}
    rs = bot.relative_strength({"XBTEUR": btc, "ETHEUR": eth, "SOLEUR": sol}, prices)

    assert set(rs) == {"ETHEUR", "SOLEUR"}  # BTC is not compared with itself
    assert rs["ETHEUR"]["ratio_now"] == pytest.approx(0.7)
    assert rs["ETHEUR"]["change_7d_pct"] > 0 and rs["ETHEUR"]["pct_vs_sma50"] > 0
    assert rs["SOLEUR"]["change_7d_pct"] < 0 and rs["SOLEUR"]["pct_vs_sma50"] < 0


def test_relative_strength_without_btc_is_empty():
    assert bot.relative_strength({"ETHEUR": make_candles(rising(60))}, {"ETHEUR": 1.0}) == {}
