"""Stops and take-profits between cycles.

A CFD broker keeps the stop and the take-profit as orders, so they fire on any touch, also on a wick between
two refreshes. The paper account replays that from Kraken's candles since the last check (1-minute candles
for gaps up to 11 hours, coarser after a longer outage):
- a long's stop fires when a candle's low reaches it, the take-profit when its high does (a short the other
  way round); when both are inside one candle the stop is assumed first (the pessimistic order),
- a candle that opens beyond the stop (a gap) fills at its open, not at the stop,
- the trigger is Kraken's trade price; the fill gets half the spread like any other close.
"""
from datetime import datetime

from krakenbot import market

from . import account


def interval_for(gap_seconds):
    if gap_seconds <= 11 * 3600:  # Kraken returns at most 720 candles
        return 1
    if gap_seconds <= 7 * 86400:
        return 15
    return 60


def fetch_candles(pair, since_ts, now):
    """Candles since `since_ts` (closed ones; the current price covers the open one)."""
    interval = interval_for(now.timestamp() - since_ts)
    days = (now.timestamp() - since_ts) / 86400 + interval / 1440
    closed, _ = market.fetch_ohlc(pair, days, interval)
    return closed, interval


def hit(pos, candle):
    """(reason, price) when the candle reaches the stop or the take-profit, else None."""
    stop, tp = pos.get("stop"), pos.get("take_profit")
    if pos["side"] == "long":
        if stop and candle["l"] <= stop:
            return "STOP", min(stop, candle["o"])
        if tp and candle["h"] >= tp:
            return "TAKE-PROFIT", max(tp, candle["o"])
    else:
        if stop and candle["h"] >= stop:
            return "STOP", max(stop, candle["o"])
        if tp and candle["l"] <= tp:
            return "TAKE-PROFIT", min(tp, candle["o"])
    return None


def check(cfg, state, prices, now=None, fetch=fetch_candles):
    """Closes the positions whose stop or take-profit was reached since the last check. `fetch` = None checks
    only the current price (offline). Returns the trades.csv rows."""
    now = now or datetime.now()
    rows = []
    for pair in list(state["positions"]):
        pos = state["positions"][pair]
        since = max(pos["opened_ts"], state.get("last_check") or 0)
        candles, interval = [], 1
        if fetch and since < now.timestamp():
            try:
                candles, interval = fetch(pair, since, now)
            except Exception as e:  # without candles the current price still protects the position
                print(f"  [{pair}] candles for the stop check unavailable: {e}")
        # a candle that ended before the position opened or before the last check is not looked at again
        candles = [c for c in candles if c["t"] + interval * 60 > since]
        if pair in prices:
            p = prices[pair]
            candles.append({"t": int(now.timestamp()), "o": p, "h": p, "l": p, "c": p})
        for candle in candles:
            # Kraken quotes gold and currencies all weekend, the broker does not: no stop fires then, and the
            # first candle after the reopen fills a stop beyond its open at that open (a gap)
            if not account.market_open(cfg, pair, datetime.fromtimestamp(candle["t"])):
                continue
            found = hit(pos, candle)
            if found:
                reason, level = found
                rows.append(account.close_position(cfg, state, pair, level, reason, now, prices))
                break
    return rows
