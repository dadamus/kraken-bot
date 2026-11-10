"""Shared helpers for the intraday paper tests (a separate module: the root conftest owns the name "conftest")."""
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# appended: kraken_bot/addon.py keeps the name "addon". kraken_cfd goes first, so "cfd" is the CFD add-on's own
# package whichever tests run first (the copy in kraken_intraday is identical, but its settings point to the
# intraday add-on's folder)
for folder in ("kraken_cfd", "kraken_intraday"):
    if str(ROOT / folder) not in sys.path:
        sys.path.append(str(ROOT / folder))

NOW = datetime(2026, 11, 4, 12, 3)  # a Wednesday, a few minutes after an hourly candle closed
FMT = "%Y-%m-%d %H:%M"


def hours(closes, now=NOW, wick=0.1):
    """Closed hourly candles ending with the one that closed at the last full hour before `now`."""
    end = int(now.replace(minute=0).timestamp()) - 3600
    start = end - (len(closes) - 1) * 3600
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append({"t": start + i * 3600, "o": prev, "h": max(prev, c) + wick, "l": min(prev, c) - wick, "c": c,
                    "v": 10.0})
        prev = c
    return out


def quiet(n=80, level=100.0, amp=0.2):
    return [level + amp * math.sin(i) for i in range(n)]


def breakout_up():
    return hours(quiet() + [101.5])


def drop():
    return hours(quiet(amp=0.05) + [98.0], wick=0.05)


def days(closes, now=NOW):
    """Closed daily candles ending yesterday."""
    end = int(datetime(now.year, now.month, now.day).timestamp()) - 86400
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append({"t": end - (len(closes) - 1 - i) * 86400, "o": prev, "h": max(prev, c) * 1.01,
                    "l": min(prev, c) * 0.99, "c": c, "v": 100.0})
        prev = c
    return out


def set_regime(state, name, book="model", pair="XBTEUR", confidence=0.8, age_hours=1, now=NOW):
    state["regimes"][book][pair] = {"regime": name, "confidence": confidence, "reasoning": "test",
                                    "date": (now - timedelta(hours=age_hours)).strftime(FMT)}
