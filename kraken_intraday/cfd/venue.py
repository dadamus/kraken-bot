"""Where a CFD call goes on Kraken Derivatives: the contract, the order size, the price levels and the costs.

The model and the risk rules work in the spot pair's prices (XBTEUR, PAXGEUR: EUR, what the candles show).
Kraken's perpetual contracts are quoted in USD (PF_XBTUSD, PF_PAXGUSD). A level is carried over at the same
distance in % from the current price: a stop 8 % under the spot price is 8 % under the contract's price.

The contract of a pair: config.json "instruments"[pair]["futures_symbols"] (the first one is traded, the
others are only measured), else PF_<base>USD. Kraken lists which platforms may trade a contract; "europa"
(the EEA entity) missing means EEA clients cannot (the currency perpetuals, e.g. PF_EURUSD).
"""
import math
from datetime import datetime

from . import futures

TAKER_FEE_PCT = 0.05  # lowest volume tier; the first 30 days without fees are not counted
INSTRUMENTS_MAX_AGE = 86400  # the contract specs are fetched once a day (the list is ~0.8 MB)


def symbols(cfg, pair):
    own = (cfg.get("instruments") or {}).get(pair) or {}
    s = own.get("futures_symbols") or own.get("futures_symbol")
    if isinstance(s, str):
        return [s]
    return list(s) if s else [f"PF_{pair[:-3]}USD"]


def symbol(cfg, pair):
    return symbols(cfg, pair)[0]


def all_symbols(cfg):
    out = []
    for pair in cfg["pairs"]:
        out += [s for s in symbols(cfg, pair) if s not in out]
    return out


def max_leverage(raw):
    """The EEA retail limit from the contract's margin schedule (initial margin 10 % -> 10x)."""
    schedule = (((raw.get("marginSchedules") or {}).get(futures.EEA) or {}).get("retail")
                or raw.get("retailMarginLevels") or [])
    im = float(schedule[0]["initialMargin"]) if schedule else 0.0
    return round(1 / im, 2) if im else None


def trim(raw):
    return {"symbol": raw["symbol"], "tick": float(raw["tickSize"]),
            "precision": int(raw.get("contractValueTradePrecision") or 0),
            "max_position": float(raw.get("maxPositionSize") or 0) or None,
            "eea": futures.EEA in (raw.get("platformsPermitted") or []),
            "tradeable": bool(raw.get("tradeable", True)) and not raw.get("suspended"),
            "max_leverage": max_leverage(raw), "quote": raw.get("quote", "USD")}


def specs(state, wanted, now=None, fetch=None):
    """{symbol: trimmed spec} for `wanted`, cached in state["kraken"]["instruments"] for a day. A symbol
    Kraken does not list is simply missing."""
    k = state.setdefault("kraken", {})
    cache = k.get("instruments") or {}
    now_ts = (now or datetime.now()).timestamp()
    have = cache.get("specs") or {}
    asked = set(cache.get("asked") or [])
    if set(wanted) - asked or now_ts - cache.get("fetched", 0) > INSTRUMENTS_MAX_AGE:
        asked = set(wanted) | asked
        got = (fetch or futures.instruments)(sorted(asked))
        have = {s: trim(raw) for s, raw in got.items()}
        k["instruments"] = {"fetched": now_ts, "asked": sorted(asked), "specs": have}
    return have


def available(spec):
    return bool(spec and spec["eea"] and spec["tradeable"])


def why_unavailable(spec, symbol_):
    if not spec:
        return f"{symbol_} is not listed on Kraken Derivatives"
    if not spec["eea"]:
        return f"{symbol_} is not offered to EEA clients"
    return f"{symbol_} is suspended"


def min_size(spec):
    return 10.0 ** -spec["precision"]


def round_size(spec, qty):
    """Down to the contract's precision (PF_ contracts: size in units of the base, 0.0001 BTC steps)."""
    f = 10 ** spec["precision"]
    return math.floor(qty * f + 1e-9) / f


def round_price(spec, price):
    tick = spec["tick"]
    decimals = max(0, -math.floor(math.log10(tick))) if tick < 1 else 0
    return round(round(price / tick) * tick, decimals)


def carry(level, spot_price, contract_price):
    """A spot-price level at the same distance in % from the contract's price."""
    return level * contract_price / spot_price


def mid(tick):
    bid, ask = float(tick.get("bid") or 0), float(tick.get("ask") or 0)
    return (bid + ask) / 2 if bid and ask else float(tick.get("markPrice") or tick.get("last"))


def spread_pct(tick):
    bid, ask = float(tick.get("bid") or 0), float(tick.get("ask") or 0)
    return (ask - bid) / ((ask + bid) / 2) * 100 if bid and ask else None


def funding_pct_day(tick):
    """What a long pays per day at the current hourly rate, in % of the notional (negative: it receives)."""
    rate = tick.get("relativeFundingRate")
    return float(rate) * 24 * 100 if rate is not None else None
