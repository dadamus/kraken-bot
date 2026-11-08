"""Indicators computed from daily candles (no network)."""
import math
from datetime import datetime, timedelta, timezone


def indicators(closed, price):
    closes = [c["c"] for c in closed]

    def chg(n):
        return (closes[-1] / closes[-1 - n] - 1) * 100 if len(closes) > n else None

    def sma(n):
        return sum(closes[-n:]) / n if len(closes) >= n else None

    def sig(x):  # 6 significant digits - enough for BTC and for cheap coins
        return float(f"{x:.6g}")

    def rsi(n=14):  # Wilder's RSI
        if len(closes) <= n:
            return None
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gain = sum(max(d, 0) for d in deltas[:n]) / n
        loss = sum(max(-d, 0) for d in deltas[:n]) / n
        for d in deltas[n:]:
            gain = (gain * (n - 1) + max(d, 0)) / n
            loss = (loss * (n - 1) + max(-d, 0)) / n
        return 100.0 if loss == 0 else round(100 - 100 / (1 + gain / loss), 1)

    def atr(n=14):  # Wilder's ATR - typical daily range, used for stop placement
        if len(closed) <= n:
            return None
        trs = [max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"]))
               for p, c in zip(closed, closed[1:])]
        a = sum(trs[:n]) / n
        for tr in trs[n:]:
            a = (a * (n - 1) + tr) / n
        return a

    def pct_vs(x):
        return round((price / x - 1) * 100, 2) if x else None

    def weekly(n=26):  # complete weeks (Mon-Sun), oldest first
        weeks = {}
        for c in closed:
            d = datetime.fromtimestamp(c["t"], timezone.utc)
            weeks.setdefault((d - timedelta(days=d.weekday())).strftime("%Y-%m-%d"), []).append(c)
        rows = [[k, sig(w[-1]["c"]), sig(max(x["h"] for x in w)), sig(min(x["l"] for x in w))]
                for k, w in sorted(weeks.items()) if len(w) == 7]
        return {"columns": ["week_start", "close", "high", "low"], "rows": rows[-n:]}

    sma200_20d_ago = sum(closes[-220:-20]) / 200 if len(closes) >= 220 else None
    a14 = atr()
    last365 = closed[-365:]
    hi365, lo365 = max(c["h"] for c in last365), min(c["l"] for c in last365)
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))][-30:]
    vol = (sum(r * r for r in rets) / len(rets)) ** 0.5 * 100 if rets else None
    y = closed[-1]
    avg_vol = sum(c["v"] for c in closed[-31:-1]) / max(1, len(closed[-31:-1]))
    last30 = closed[-30:]
    hi30, lo30 = max(c["h"] for c in last30), min(c["l"] for c in last30)
    vol30 = sum(c["v"] for c in last30) / len(last30)
    return {
        "price_now": round(price, 6),
        "yesterday": {"open": y["o"], "high": y["h"], "low": y["l"], "close": y["c"],
                      "volume_vs_30d_avg": round(y["v"] / avg_vol, 2) if avg_vol else None},
        "change_1d_pct": round(chg(1), 2) if chg(1) is not None else None,
        "change_7d_pct": round(chg(7), 2) if chg(7) is not None else None,
        "change_30d_pct": round(chg(30), 2) if chg(30) is not None else None,
        "change_90d_pct": round(chg(90), 2) if chg(90) is not None else None,
        "change_365d_pct": round(chg(365), 2) if chg(365) is not None else None,
        "sma7": round(sma(7), 6) if sma(7) else None,
        "sma30": round(sma(30), 6) if sma(30) else None,
        "sma50": sig(sma(50)) if sma(50) else None,
        "sma200": sig(sma(200)) if sma(200) else None,
        "pct_vs_sma50": pct_vs(sma(50)),
        "pct_vs_sma200": pct_vs(sma(200)),
        "sma200_change_20d_pct": round((sma(200) / sma200_20d_ago - 1) * 100, 2) if sma200_20d_ago else None,
        "atr14": sig(a14) if a14 else None,
        "atr14_pct": round(a14 / price * 100, 2) if a14 else None,
        "daily_volatility_pct": round(vol, 2) if vol else None,
        "rsi14": rsi(),
        "range_30d": {"high": sig(hi30), "low": sig(lo30),
                      "pct_below_high": round((1 - price / hi30) * 100, 2),
                      "pct_above_low": round((price / lo30 - 1) * 100, 2)},
        "range_365d": {"days": len(last365), "high": sig(hi365), "low": sig(lo365),
                       "pct_below_high": round((1 - price / hi365) * 100, 2),
                       "pct_above_low": round((price / lo365 - 1) * 100, 2)},
        "last_26_weeks": weekly(),
        "last_30_days": {  # oldest first; volume_rel = volume / average of these 30 days
            "columns": ["date", "close", "high", "low", "volume_rel"],
            "rows": [[datetime.fromtimestamp(c["t"], timezone.utc).strftime("%m-%d"), sig(c["c"]),
                      sig(c["h"]), sig(c["l"]), round(c["v"] / vol30, 2)] for c in last30]},
    }


def hourly(candles, price):
    """The last 24 h from hourly candles (oldest first, UTC): where a broken level was reclaimed or lost,
    whether a drop accelerates or stalls."""
    if not candles:
        return None

    def sig(x):
        return float(f"{x:.6g}")

    hi, lo = max(c["h"] for c in candles), min(c["l"] for c in candles)
    avg_vol = sum(c["v"] for c in candles) / len(candles)
    return {"change_pct": round((price / candles[0]["o"] - 1) * 100, 2),
            "high": sig(hi), "low": sig(lo),
            "pct_below_high": round((1 - price / hi) * 100, 2),
            "pct_above_low": round((price / lo - 1) * 100, 2),
            "columns": ["hour_utc", "open", "close", "high", "low", "volume_rel"],
            "rows": [[datetime.fromtimestamp(c["t"], timezone.utc).strftime("%m-%d %H:00"), sig(c["o"]),
                      sig(c["c"]), sig(c["h"]), sig(c["l"]),
                      round(c["v"] / avg_vol, 2) if avg_vol else None] for c in candles]}


def relative_strength(closed_by_pair, prices, base="XBTEUR"):
    """Each pair priced in BTC (e.g. ETH/BTC) from our EUR candles: is the coin stronger than the market?"""
    if base not in closed_by_pair:
        return {}
    btc = {c["t"]: c["c"] for c in closed_by_pair[base]}
    out = {}
    for pair, cs in closed_by_pair.items():
        if pair == base:
            continue
        ratio = [c["c"] / btc[c["t"]] for c in cs if c["t"] in btc]
        now = prices[pair] / prices[base]

        def chg(n):
            return round((now / ratio[-n] - 1) * 100, 2) if len(ratio) >= n else None

        sma50 = sum(ratio[-50:]) / 50 if len(ratio) >= 50 else None
        out[pair] = {"ratio_now": float(f"{now:.6g}"), "change_7d_pct": chg(7), "change_30d_pct": chg(30),
                     "change_90d_pct": chg(90),
                     "pct_vs_sma50": round((now / sma50 - 1) * 100, 2) if sma50 else None}
    return out
