"""Where an instrument's prices come from: Kraken's spot market (the default: XBTEUR, PAXGEUR, EURUSD) or, for an
instrument Kraken has no spot market for, its Kraken Derivatives contract (instruments.<pair>.source:
"futures": the contract in futures_symbols, its mark price).

Crude oil is such an instrument: WTIUSD is priced from PF_WTIOILUSD, which follows a 24/7 index of WTI crude
oil (Pyth). The mark price is what Kraken triggers stops on, so the paper account sees the prices a live
position would. Its history starts with the contract (2026-05-28): indicators that need more days (SMA200,
the yearly range) stay empty until there are enough candles.

use(cfg) has to run before the prices are fetched (the cycle does it): the stop check's candle fetch
(guard.fetch_candles) only gets a pair name.
"""
import time

import requests

from krakenbot import config as kb_config
from krakenbot import market

from . import futures, venue

CHARTS = "/api/charts/v1"  # /mark (prices; no volume) and /trade (the volume)
RESOLUTION = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 240: "4h", 720: "12h", 1440: "1d", 10080: "1w"}
FUTURES = {}  # pair -> contract, for the pairs priced from a derivatives contract


def use(cfg):
    FUTURES.clear()
    for pair, own in (cfg.get("instruments") or {}).items():
        if (own or {}).get("source") == "futures":
            FUTURES[pair] = venue.symbol(cfg, pair)
    return dict(FUTURES)


def chart(tick, symbol, days, interval, now):
    res = RESOLUTION[interval]
    since, out = int(now - days * 86400), []
    for _ in range(20):  # the API pages long ranges ("more_candles")
        r = requests.get(f"{futures.BASE}{CHARTS}/{tick}/{symbol}/{res}", params={"from": since, "to": int(now)},
                         headers=kb_config.UA, timeout=20)
        r.raise_for_status()
        data = r.json()
        page = [{"t": int(c["time"]) // 1000, "o": float(c["open"]), "h": float(c["high"]), "l": float(c["low"]),
                 "c": float(c["close"]), "v": float(c.get("volume") or 0)} for c in data.get("candles") or []]
        out += [c for c in page if not out or c["t"] > out[-1]["t"]]
        if not data.get("more_candles") or not page:
            break
        since = page[-1]["t"] + 1
    if not out:
        raise RuntimeError(f"Kraken Futures charts {tick} {symbol} {res}: no candles")
    return out


def contract_candles(symbol, days, interval, now=None, volume=True):
    """A contract's mark-price candles from Kraken's charts API, with the traded volume (the mark price has
    none; the indicators compare volumes): (closed candles, current price)."""
    now = now or time.time()
    out = chart("mark", symbol, days, interval, now)
    if volume:
        traded = {c["t"]: c["v"] for c in chart("trade", symbol, days, interval, now)}
        out = [{**c, "v": traded.get(c["t"], 0.0)} for c in out]
    closed = [c for c in out if c["t"] + interval * 60 <= now]
    return closed, out[-1]["c"]


def ohlc(pair, days=60, interval=1440, volume=True):
    """(closed candles, current price) like krakenbot.market.fetch_ohlc. volume=False: the stop check, which
    needs only prices (one request less for a contract)."""
    if pair in FUTURES:
        return contract_candles(FUTURES[pair], days, interval, volume=volume)
    return market.fetch_ohlc(pair, days, interval)


def hourly(pair, hours=24):
    closed, _ = ohlc(pair, days=(hours + 2) / 24, interval=60)
    return closed[-hours:]


def price(pair):
    if pair in FUTURES:
        return float(futures.ticker(FUTURES[pair])["markPrice"])
    return market.fetch_price(pair)
