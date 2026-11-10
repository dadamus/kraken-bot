"""The day's regime per instrument and what it lets the strategies do.

  trend_up    breakouts above the hourly channel, long only
  trend_down  breakdowns below the hourly channel, short only
  range       fading stretched moves back to the mean (Bollinger bands and RSI), both directions
  no_trade    no new positions; an open one is closed at the next hourly step

The model book gets its regimes from Claude once a day. The rules book gets them from a fixed rule on daily
candles, the same every day, so the two books can be compared: does the model's judgement add anything to a
simple filter?
"""
from . import ta

REGIMES = ("trend_up", "trend_down", "range", "no_trade")
ALLOWED = {"trend_up": (("breakout", "long"),), "trend_down": (("breakout", "short"),),
           "range": (("reversion", "long"), ("reversion", "short")), "no_trade": ()}


def allowed(cfg, name):
    """The (strategy, side) pairs the regime allows, without disabled strategies, and without shorts when
    allow_short is off."""
    strategies = cfg["strategies"]
    return [(s, side) for s, side in ALLOWED.get(name, ())
            if strategies.get(s, {}).get("enabled", True) and (side == "long" or cfg.get("allow_short", True))]


def pct(a, b):
    return (a / b - 1) * 100


def rules(cfg, daily):
    """The rule: trend_up when the last daily close is above SMA50, SMA20 is above SMA50 and the daily ADX is at
    least adx_trend; trend_down the mirror; otherwise range. `daily`: closed daily candles, oldest first.
    Returns {"regime", "reasoning"}."""
    closes = [c["c"] for c in daily]
    strength = ta.adx(daily[-120:], 14)
    if len(closes) < 50 or strength is None:
        return {"regime": "no_trade", "reasoning": "fewer than 50 daily candles"}
    sma20, sma50 = sum(closes[-20:]) / 20, sum(closes[-50:]) / 50
    last = closes[-1]
    note = (f"close {pct(last, sma50):+.1f}% vs SMA50, SMA20 {pct(sma20, sma50):+.1f}% vs SMA50,"
            f" daily ADX {strength:.0f}")
    if strength >= cfg["intraday"]["rules_regime"]["adx_trend"]:
        if last > sma50 and sma20 > sma50:
            return {"regime": "trend_up", "reasoning": note}
        if last < sma50 and sma20 < sma50:
            return {"regime": "trend_down", "reasoning": note}
    return {"regime": "range", "reasoning": note}
