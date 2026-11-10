"""Public market data: Kraken candles and ticker, news, futures, sentiment, CoinGecko, Deribit.
Optional sources go through optional(): a failing one does not stop the cycle."""
import math
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from . import config
from .i18n import t


def fetch_ohlc(pair, days=60, interval=1440):
    """Candles (interval in minutes, 1440 = daily). Returns (closed candles, current price)."""
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    r = requests.get(f"{config.KRAKEN}/0/public/OHLC",
                     params={"pair": pair, "interval": interval, "since": since},
                     headers=config.UA, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"Kraken: {data['error']}")
    key = next(k for k in data["result"] if k != "last")
    rows = data["result"][key]
    # [time, open, high, low, close, vwap, volume, count]; the last candle is still open
    candles = [{"t": int(c[0]), "o": float(c[1]), "h": float(c[2]), "l": float(c[3]),
                "c": float(c[4]), "v": float(c[6])} for c in rows]
    return candles[:-1], candles[-1]["c"]  # (closed candles, current price)


def fetch_hourly(pair, hours=24):
    """The last `hours` closed hourly candles: the intraday path the daily candles do not show."""
    closed, _ = fetch_ohlc(pair, days=(hours + 2) / 24, interval=60)
    return closed[-hours:]


def fetch_price(pair):
    r = requests.get(f"{config.KRAKEN}/0/public/Ticker", params={"pair": pair}, headers=config.UA, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"Kraken: {data['error']}")
    return float(next(iter(data["result"].values()))["c"][0])  # c = last trade


def fetch_news(feeds, hours=30, limit=40):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    items = []
    for url in feeds:
        try:
            r = requests.get(url, headers=config.UA, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                pub = it.findtext("pubDate")
                try:
                    dt = parsedate_to_datetime(pub) if pub else None
                except Exception:
                    dt = None
                if title and (dt is None or dt >= cutoff):
                    items.append(title)
        except Exception as e:
            print(t("source_skipped", name=f"news {url}", err=e))
    seen, out = set(), []
    for title in items:
        if title.lower() not in seen:
            seen.add(title.lower())
            out.append(title)
    return out[:limit]


def fetch_derivatives(pairs, state):
    """Funding and open interest of perpetual contracts from Kraken Futures (USD market, same sentiment)."""
    r = requests.get(config.KRAKEN_FUTURES, headers=config.UA, timeout=20)
    r.raise_for_status()
    tickers = {x["symbol"]: x for x in r.json()["tickers"]}
    prev = state.setdefault("open_interest", {})
    out = {}
    for pair in pairs:
        x = tickers.get(f"PF_{pair[:-3]}USD")  # XBTEUR -> PF_XBTUSD
        if not x:
            continue
        old = prev.get(pair)
        out[pair] = {  # Kraken reports hourly funding; converted to the customary % per 8h
            "funding_8h_pct": round(x["relativeFundingRate"] * 8 * 100, 4),
            "funding_next_8h_pct": round(x["relativeFundingRatePrediction"] * 8 * 100, 4),
            "open_interest_usd_m": round(x["openInterest"] * x["markPrice"] / 1e6, 1),
            "open_interest_change_pct": round((x["openInterest"] / old["coins"] - 1) * 100, 2) if old else None,
            "open_interest_change_since": old["date"] if old else None,
            "perp_premium_pct": round((x["markPrice"] / x["indexPrice"] - 1) * 100, 3),
        }
        prev[pair] = {"coins": x["openInterest"], "date": datetime.now().strftime("%Y-%m-%d %H:%M")}
    return out


def fetch_fear_greed(days=7):
    r = requests.get(config.FEAR_GREED, params={"limit": days}, headers=config.UA, timeout=15)
    r.raise_for_status()
    data = r.json()["data"]
    return {"today": int(data[0]["value"]), "label": data[0]["value_classification"],
            "last_days_newest_first": [int(d["value"]) for d in data]}


def fetch_market_overview(state):
    """The whole crypto market from CoinGecko: BTC/ETH dominance, stablecoin share (rising share = flight
    to cash), market cap change. Changes are measured against the previous cycle."""
    r = requests.get(config.COINGECKO_GLOBAL, headers=config.UA, timeout=15)
    r.raise_for_status()
    d = r.json()["data"]
    share = d["market_cap_percentage"]
    now = {"btc_dominance_pct": round(share.get("btc", 0), 2),
           "eth_dominance_pct": round(share.get("eth", 0), 2),
           "stablecoin_share_pct": round(share.get("usdt", 0) + share.get("usdc", 0), 2),
           "total_market_cap_usd_bn": round(d["total_market_cap"]["usd"] / 1e9, 1)}
    prev = state.get("market_overview")
    out = {**now, "total_market_cap_change_24h_pct": round(d.get("market_cap_change_percentage_24h_usd", 0), 2)}
    if prev:
        out["change_since"] = prev["date"]
        out["changes_since_last_cycle"] = {k: round(now[k] - prev[k], 2) for k in now if k in prev}
    state["market_overview"] = {**now, "date": datetime.now().strftime("%Y-%m-%d %H:%M")}
    return out


def fetch_implied_vol(pairs, closed_by_pair, days=90):
    """Deribit DVOL index: annualised implied volatility from options (BTC, ETH) - how big a move the
    market prices in. Compared with the realised volatility of the last 30 days."""
    now = int(time.time() * 1000)
    out = {}
    for pair in pairs:
        cur = {"XBT": "BTC"}.get(pair[:-3], pair[:-3])
        r = requests.get(config.DERIBIT_DVOL, headers=config.UA, timeout=15,
                         params={"currency": cur, "resolution": "1D", "end_timestamp": now,
                                 "start_timestamp": now - days * 86400 * 1000})
        r.raise_for_status()
        rows = r.json()["result"]["data"]
        if not rows:  # Deribit has no DVOL for this coin (e.g. SOL)
            continue
        closes = [x[4] for x in rows]
        dvol = closes[-1]
        cl = [c["c"] for c in closed_by_pair.get(pair, [])][-31:]
        rets = [math.log(b / a) for a, b in zip(cl, cl[1:])]
        realized = (sum(x * x for x in rets) / len(rets)) ** 0.5 * math.sqrt(365) * 100 if rets else None
        out[pair] = {"dvol_now": dvol,
                     "implied_daily_move_pct": round(dvol / math.sqrt(365), 2),
                     "dvol_7d_ago": closes[-8] if len(closes) >= 8 else None,
                     f"dvol_{days}d_min": min(closes), f"dvol_{days}d_max": max(closes),
                     "realized_vol_30d_annual_pct": round(realized, 1) if realized else None}
    return out


def optional(name, fn, *args):
    """Extra data: a failing source does not stop the cycle, the model simply gets less information."""
    try:
        return fn(*args)
    except Exception as e:
        print(t("source_skipped", name=name, err=e))
        return None
