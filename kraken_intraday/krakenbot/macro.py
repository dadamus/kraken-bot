"""Context from outside crypto (no keys): US markets and rates (FRED), the week's macro calendar
(ForexFactory feed) and the Coinbase premium (US spot demand). Each goes through market.optional()."""
import csv
import io
from datetime import datetime, timedelta, timezone

import requests

from . import config, market

# FRED series: daily closes, published with a lag of a day (the dollar index about a week)
FRED_SERIES = {"nasdaq100": "NASDAQ100", "sp500": "SP500", "vix": "VIXCLS",
               "us10y_yield_pct": "DGS10", "usd_index_broad": "DTWEXBGS"}
LEVELS = {"vix", "us10y_yield_pct"}  # changes in points, not percent


def series_summary(rows, level=False):
    """rows: [(date, value)] oldest first, without gaps. Changes over 1, 5 and 20 trading days."""
    if not rows:
        return None
    date, last = rows[-1]

    def chg(n):
        if len(rows) <= n:
            return None
        old = rows[-1 - n][1]
        return round(last - old, 2) if level else round((last / old - 1) * 100, 2)

    unit = "points" if level else "pct"
    return {"last": last, "date": date, f"change_1d_{unit}": chg(1), f"change_5d_{unit}": chg(5),
            f"change_20d_{unit}": chg(20)}


def fetch_fred(series_id, days=60):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    r = requests.get(config.FRED_CSV, params={"id": series_id, "cosd": since}, headers=config.UA, timeout=20)
    r.raise_for_status()
    rows = []
    for row in list(csv.reader(io.StringIO(r.text)))[1:]:
        try:  # holidays are "." or empty
            rows.append((row[0], float(row[1])))
        except (IndexError, ValueError):
            continue
    return rows


def fetch_us_markets():
    """Nasdaq 100, S&P 500, VIX, 10-year yield and the broad dollar index: BTC trades with risk assets."""
    out = {}
    for name, series_id in FRED_SERIES.items():
        summary = market.optional(f"FRED {series_id}", lambda s=series_id, n=name:
                                  series_summary(fetch_fred(s), n in LEVELS))
        if summary:
            out[name] = summary
    return out or None


def calendar_events(events, now, past_hours=24):
    """High-impact US events from the past `past_hours` to the end of the feed (the current week)."""
    out = []
    for e in events:
        if e.get("country") != "USD" or e.get("impact") != "High":
            continue
        try:
            when = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        if when < now - timedelta(hours=past_hours):
            continue
        out.append({"time_utc": when.strftime("%Y-%m-%d %H:%M"), "event": e.get("title", ""),
                    "in_hours": round((when - now).total_seconds() / 3600, 1),
                    "forecast": e.get("forecast") or None, "previous": e.get("previous") or None})
    return sorted(out, key=lambda x: x["time_utc"])


def fetch_macro_calendar(state, refresh_hours=6):
    """The feed allows only a few requests per minutes and changes rarely: the week's US high-impact events
    are kept in the state and downloaded again at most every refresh_hours (or when the week changed)."""
    now = datetime.now(timezone.utc)
    cache = state.get("macro_calendar_cache") or {}
    week = now.strftime("%G-W%V")
    fresh = cache.get("week") == week and cache.get("fetched", "") >=         (now - timedelta(hours=refresh_hours)).strftime("%Y-%m-%d %H:%M")
    if not fresh:
        try:
            r = requests.get(config.MACRO_CALENDAR, headers=config.UA, timeout=20)
            r.raise_for_status()
            events = [e for e in r.json() if e.get("country") == "USD" and e.get("impact") == "High"]
            cache = {"week": week, "fetched": now.strftime("%Y-%m-%d %H:%M"), "events": events}
            state["macro_calendar_cache"] = cache
        except Exception:
            if cache.get("week") != week:  # last week's events are no use
                raise
    return calendar_events(cache["events"], now)


def premium_summary(coinbase, kraken, now_cb, now_kr):
    """coinbase / kraken: {hour_ts: close} in USD. Premium in % = Coinbase / Kraken - 1."""
    common = sorted(set(coinbase) & set(kraken))[-24:]
    hourly = [(coinbase[t] / kraken[t] - 1) * 100 for t in common]
    return {"premium_now_pct": round((now_cb / now_kr - 1) * 100, 3),
            "premium_avg_24h_pct": round(sum(hourly) / len(hourly), 3) if hourly else None,
            "premium_last_6h_pct": [round(x, 3) for x in hourly[-6:]]}


def fetch_coinbase_premium():
    """BTC on Coinbase (US buyers, the ETFs' custodian) versus Kraken, both in USD."""
    base = config.COINBASE
    r = requests.get(f"{base}/products/BTC-USD/candles", params={"granularity": 3600},
                     headers=config.UA, timeout=20)
    r.raise_for_status()
    coinbase = {int(c[0]): float(c[4]) for c in r.json()}  # [time, low, high, open, close, volume]
    r = requests.get(f"{base}/products/BTC-USD/ticker", headers=config.UA, timeout=20)
    r.raise_for_status()
    now_cb = float(r.json()["price"])
    closed, now_kr = market.fetch_ohlc("XBTUSD", days=2, interval=60)
    return premium_summary(coinbase, {c["t"]: c["c"] for c in closed}, now_cb, now_kr)
