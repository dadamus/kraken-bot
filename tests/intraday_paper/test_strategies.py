"""Indicators, the regime rule and the two strategies with the cost filter and the trailing stop."""
import pytest
from .intraday_helpers import breakout_up, days, drop, hours, quiet

from intraday import regime, strategies, ta


def test_indicators_on_known_series():
    closes = [float(x) for x in range(1, 31)]
    assert ta.ema(closes, 10) == pytest.approx(25.5)  # a straight line: the EMA lags by (n-1)/2
    assert ta.rsi(closes, 14) == 100.0
    lower, mid, upper = ta.bands([10.0] * 20, 20, 2)
    assert lower == mid == upper == 10.0
    cs = hours([100.0] * 30, wick=0.5)
    assert ta.atr(cs, 14) == pytest.approx(1.0)
    assert ta.channel(cs, 5) == (100.5, 99.5)
    trend = hours([100.0 + i for i in range(40)])
    assert ta.adx(trend, 14) > 90  # one-way moves: a strong trend
    assert ta.adx(hours(quiet(40)), 14) < ta.adx(trend, 14)
    assert ta.ema(closes[:5], 10) is None and ta.adx(trend[:20], 14) is None


def test_rule_regime_follows_the_daily_trend(icfg):
    up = days([100 * 1.01 ** i for i in range(80)])
    down = days([100 * 0.99 ** i for i in range(80)])
    assert regime.rules(icfg, up)["regime"] == "trend_up"
    assert regime.rules(icfg, down)["regime"] == "trend_down"
    assert regime.rules(icfg, days(quiet(80, amp=2)))["regime"] == "range"
    assert regime.rules(icfg, up[-30:])["regime"] == "no_trade"  # too little history


def test_allowed_respects_disabled_strategies_and_allow_short(icfg):
    assert regime.allowed(icfg, "range") == [("reversion", "long"), ("reversion", "short")]
    icfg["allow_short"] = False
    assert regime.allowed(icfg, "trend_down") == []
    assert regime.allowed(icfg, "range") == [("reversion", "long")]
    icfg["strategies"]["breakout"]["enabled"] = False
    assert regime.allowed(icfg, "trend_up") == []


def test_fresh_breakout_gives_a_long_with_atr_levels(icfg):
    cs = breakout_up()
    prop, note = strategies.signal(icfg, "XBTEUR", "trend_up", cs, 101.5)
    assert note is None and prop["strategy"] == "breakout" and prop["side"] == "long"
    atr_h = strategies.hourly_atr(icfg, cs)
    assert prop["dist"] == pytest.approx(1.5 * atr_h)
    assert prop["stop"] == pytest.approx(101.5 - prop["dist"], rel=1e-5)
    assert prop["take_profit"] == pytest.approx(101.5 + 2 * prop["dist"], rel=1e-5)


def test_breakout_needs_a_fresh_close_beyond_the_channel_and_the_trend_regime(icfg):
    stale = hours(quiet() + [101.5, 101.8])  # the hour after the breakout: not fresh any more
    prop, note = strategies.signal(icfg, "XBTEUR", "trend_up", stale, 101.8)
    assert prop is None and "not a fresh breakout" in note
    assert strategies.signal(icfg, "XBTEUR", "trend_up", hours(quiet()), 100.0)[0] is None
    prop, note = strategies.signal(icfg, "XBTEUR", "trend_down", breakout_up(), 101.5)
    assert prop is None and "no breakdown" in note
    # the same spike in a range regime is a stretch to fade, not a breakout to follow
    prop, _ = strategies.signal(icfg, "XBTEUR", "range", breakout_up(), 101.5)
    assert prop["strategy"] == "reversion" and prop["side"] == "short"


def test_breakdown_gives_a_short_in_trend_down(icfg):
    cs = hours(quiet() + [98.5])
    prop, _ = strategies.signal(icfg, "XBTEUR", "trend_down", cs, 98.5)
    assert prop["side"] == "short" and prop["stop"] > 98.5 > prop["take_profit"]


def test_reversion_fades_a_stretched_drop_to_the_middle_band(icfg):
    cs = drop()
    prop, note = strategies.signal(icfg, "XBTEUR", "range", cs, 98.0)
    assert note is None and prop["strategy"] == "reversion" and prop["side"] == "long"
    _, mid, _ = ta.bands([c["c"] for c in cs], 20, 2)
    assert prop["take_profit"] == pytest.approx(mid, rel=1e-5)
    assert prop["stop"] < 98.0
    assert strategies.signal(icfg, "XBTEUR", "trend_up", cs, 98.0)[0] is None


def test_a_target_too_close_for_the_spread_is_skipped(icfg):
    icfg["spread_pct"]["XBTEUR"] = 1.0  # 4x the spread = a 4 % target, far beyond a 1.5 % move
    prop, note = strategies.signal(icfg, "XBTEUR", "trend_up", breakout_up(), 101.5)
    assert prop is None and "the target" in note and "4x the 1% spread" in note


def test_trailing_stop_starts_after_one_r_and_only_moves_closer(icfg):
    pos = {"strategy": "breakout", "side": "long", "entry_mid": 100.0, "risk_dist": 1.0, "stop": 99.0}
    assert strategies.trail(icfg, pos, 100.5, 0.4) is None and pos["stop"] == 99.0  # +0.5R
    assert strategies.trail(icfg, pos, 101.5, 0.4) == pytest.approx(100.7)  # best 101.5 - 2 x 0.4
    assert strategies.trail(icfg, pos, 101.0, 0.4) is None  # a lower close does not move it back
    short = {"strategy": "breakout", "side": "short", "entry_mid": 100.0, "risk_dist": 1.0, "stop": 101.0}
    assert strategies.trail(icfg, short, 98.0, 0.4) == pytest.approx(98.8)
    assert strategies.trail(icfg, {**pos, "strategy": "reversion"}, 105.0, 0.4) is None
