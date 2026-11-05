"""Fake data for --offline: a test run without internet or API keys."""
import math
import random
import time

from .i18n import t


def fake_market(start_price):
    price, closed = start_price, []
    start = int(time.time()) - 61 * 86400
    for i in range(60):
        o = price
        price *= math.exp(random.gauss(0, 0.03))
        closed.append({"t": start + i * 86400, "o": o, "h": max(o, price) * 1.01,
                       "l": min(o, price) * 0.99, "c": price, "v": random.uniform(800, 1500)})
    return closed, price * math.exp(random.gauss(0, 0.01))


def fake_signal():
    return {"action": random.choice(["buy", "sell", "hold"]),
            "confidence": round(random.uniform(0.3, 0.9), 2),
            "p_up_3d": round(random.uniform(0.35, 0.65), 2),
            "size_pct": random.choice([25, 50, 100]),
            "stop_price": None,
            "reasoning": t("offline_reasoning")}
