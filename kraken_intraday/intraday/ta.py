"""Indicators on candles (no network). Candles are dicts {t, o, h, l, c, v}, oldest first; every function
returns the value at the last candle, or None while there are too few candles."""
import math


def ema(values, n):
    """Exponential moving average of the whole series, seeded with the mean of the first n values."""
    if len(values) < n:
        return None
    k = 2 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def true_ranges(candles):
    return [max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"])) for p, c in zip(candles, candles[1:])]


def atr(candles, n=14):
    """Wilder's average true range: the typical range of one candle."""
    if len(candles) <= n:
        return None
    trs = true_ranges(candles)
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def rsi(closes, n=14):
    """Wilder's RSI, 0-100."""
    if len(closes) <= n:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gain = sum(max(d, 0) for d in deltas[:n]) / n
    loss = sum(max(-d, 0) for d in deltas[:n]) / n
    for d in deltas[n:]:
        gain = (gain * (n - 1) + max(d, 0)) / n
        loss = (loss * (n - 1) + max(-d, 0)) / n
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def bands(closes, n=20, k=2.0):
    """Bollinger bands of the last n closes: (lower, middle, upper)."""
    if len(closes) < n:
        return None
    window = closes[-n:]
    mid = sum(window) / n
    sd = math.sqrt(sum((x - mid) ** 2 for x in window) / n)
    return mid - k * sd, mid, mid + k * sd


def channel(candles, n):
    """(highest high, lowest low) of the last n candles."""
    if len(candles) < n:
        return None
    window = candles[-n:]
    return max(c["h"] for c in window), min(c["l"] for c in window)


def adx(candles, n=14):
    """Wilder's ADX: how strongly the price trends, 0-100, whatever the direction (above about 20-25 a trend)."""
    if len(candles) < 2 * n + 1:
        return None
    trs = true_ranges(candles)
    plus, minus = [], []
    for p, c in zip(candles, candles[1:]):
        up, down = c["h"] - p["h"], p["l"] - c["l"]
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
    s_tr, s_plus, s_minus = sum(trs[:n]), sum(plus[:n]), sum(minus[:n])
    dxs = []
    for i in range(n, len(trs) + 1):
        if i > n:
            s_tr += trs[i - 1] - s_tr / n
            s_plus += plus[i - 1] - s_plus / n
            s_minus += minus[i - 1] - s_minus / n
        di_plus, di_minus = (100 * s_plus / s_tr, 100 * s_minus / s_tr) if s_tr else (0.0, 0.0)
        total = di_plus + di_minus
        dxs.append(100 * abs(di_plus - di_minus) / total if total else 0.0)
    a = sum(dxs[:n]) / n
    for dx in dxs[n:]:
        a = (a * (n - 1) + dx) / n
    return a
