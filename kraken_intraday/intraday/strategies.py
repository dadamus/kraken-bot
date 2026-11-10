"""The hourly strategies. A signal is checked once per hourly candle, right after it closes, on closed candles
only; the levels are set from the current price (the fill).

breakout (the trend regimes)
  long   the candle closes above the highest high of the channel_hours candles before it, the candle before
         it closed inside its own channel (a fresh breakout, not the fifth hour of one) and the close is above
         the ema_hours EMA; short is the mirror below the lowest low
  exit   stop stop_atr hourly ATRs away, take-profit reward_risk x the stop distance; once the best close is
         1R in profit the stop trails trail_atr hourly ATRs behind it
reversion (the range regime)
  long   the candle closes below the lower Bollinger band (band_hours, band_std), the candle before closed
         inside the bands, and RSI (rsi_hours) is below rsi_low; short is the mirror above the upper band with
         RSI above rsi_high
  exit   take-profit at the middle band (the mean it should revert to), stop stop_atr hourly ATRs away

Every signal must also clear the costs: the take-profit must be at least min_target_to_cost times the
instrument's round-trip spread away, otherwise the trade would mostly pay the broker.
"""
from cfd import account
from cfd.risk import sig

from . import regime, ta


def hourly_atr(cfg, candles):
    return ta.atr(candles, cfg["intraday"]["atr_hours"])


def levels(side, price, dist, target):
    sign = 1 if side == "long" else -1
    return sig(price - sign * dist), sig(target)


def breakout(cfg, side, candles, price, atr_h):
    p = cfg["strategies"]["breakout"]
    n = p["channel_hours"]
    if len(candles) < max(n + 2, p["ema_hours"]):
        return None, "not enough hourly candles"
    closes = [c["c"] for c in candles]
    last, prev = closes[-1], closes[-2]
    hi, lo = ta.channel(candles[:-1], n)
    hi_prev, lo_prev = ta.channel(candles[:-2], n)
    trend = ta.ema(closes, p["ema_hours"])
    if side == "long":
        if last <= hi:
            return None, f"no breakout (close {last:g}, {n}h high {hi:g})"
        if prev > hi_prev:
            return None, "above the channel for more than an hour (not a fresh breakout)"
        if last <= trend:
            return None, f"breakout below the {p['ema_hours']}h EMA"
        level = hi
    else:
        if last >= lo:
            return None, f"no breakdown (close {last:g}, {n}h low {lo:g})"
        if prev < lo_prev:
            return None, "below the channel for more than an hour (not a fresh breakdown)"
        if last >= trend:
            return None, f"breakdown above the {p['ema_hours']}h EMA"
        level = lo
    dist = p["stop_atr"] * atr_h
    sign = 1 if side == "long" else -1
    stop, tp = levels(side, price, dist, price + sign * p["reward_risk"] * dist)
    return {"strategy": "breakout", "side": side, "stop": stop, "take_profit": tp, "dist": dist,
            "why": f"breakout {side}: close {last:g} beyond the {n}h {'high' if side == 'long' else 'low'} {level:g}"}, None


def reversion(cfg, side, candles, price, atr_h):
    p = cfg["strategies"]["reversion"]
    n = p["band_hours"]
    if len(candles) < max(n + 1, p["rsi_hours"] + 1):
        return None, "not enough hourly candles"
    closes = [c["c"] for c in candles]
    last, prev = closes[-1], closes[-2]
    lower, mid, upper = ta.bands(closes, n, p["band_std"])
    lower_prev, _, upper_prev = ta.bands(closes[:-1], n, p["band_std"])
    strength = ta.rsi(closes, p["rsi_hours"])
    if side == "long":
        if last >= lower:
            return None, f"inside the bands (close {last:g}, lower {sig(lower):g})"
        if prev < lower_prev:
            return None, "below the lower band for more than an hour (not a fresh stretch)"
        if strength >= p["rsi_low"]:
            return None, f"below the band but RSI {strength:.0f} >= {p['rsi_low']}"
        if mid <= price:
            return None, "the price is already back at the middle band"
    else:
        if last <= upper:
            return None, f"inside the bands (close {last:g}, upper {sig(upper):g})"
        if prev > upper_prev:
            return None, "above the upper band for more than an hour (not a fresh stretch)"
        if strength <= p["rsi_high"]:
            return None, f"above the band but RSI {strength:.0f} <= {p['rsi_high']}"
        if mid >= price:
            return None, "the price is already back at the middle band"
    dist = p["stop_atr"] * atr_h
    stop, tp = levels(side, price, dist, mid)
    band = "lower" if side == "long" else "upper"
    return {"strategy": "reversion", "side": side, "stop": stop, "take_profit": tp, "dist": dist,
            "why": f"reversion {side}: close {last:g} beyond the {band} band, RSI {strength:.0f}"}, None


SIGNALS = {"breakout": breakout, "reversion": reversion}


def signal(cfg, pair, regime_name, candles, price):
    """The first signal the regime allows that clears the costs: (proposal, None), or (None, why not).
    A proposal: {strategy, side, stop, take_profit, dist (the stop distance), why}."""
    atr_h = hourly_atr(cfg, candles)
    if not atr_h:
        return None, "not enough hourly candles"
    notes = []
    for strategy, side in regime.allowed(cfg, regime_name):
        prop, note = SIGNALS[strategy](cfg, side, candles, price, atr_h)
        if not prop:
            notes.append(note)
            continue
        spread = account.spread_pct(cfg, pair)
        target = abs(prop["take_profit"] - price) / price * 100
        need = cfg["intraday"]["min_target_to_cost"] * spread
        if target < need:
            notes.append(f"{prop['why']}, but the target {target:.2f}% < {need:.2f}% "
                         f"({cfg['intraday']['min_target_to_cost']:g}x the {spread:g}% spread)")
            continue
        return prop, None
    return None, "; ".join(notes) or f"{regime_name}: nothing allowed"


def trail(cfg, pos, close, atr_h):
    """A breakout position's trailing stop, after a closed hourly candle: remembers the best close, and once
    it is 1R (the initial stop distance) in profit moves the stop to trail_atr hourly ATRs behind it - only
    ever closer. Returns the new stop or None."""
    if pos.get("strategy") != "breakout" or not atr_h:
        return None
    sign = 1 if pos["side"] == "long" else -1
    best = pos.get("best") or pos["entry_mid"]
    best = max(best, close) if sign == 1 else min(best, close)
    pos["best"] = best
    if (best - pos["entry_mid"]) * sign < (pos.get("risk_dist") or float("inf")):
        return None
    new = sig(best - sign * cfg["strategies"]["breakout"]["trail_atr"] * atr_h)
    if (new - pos["stop"]) * sign > 0:
        pos["stop"] = new
        return new
    return None
