"""Chart data for the dashboard: recent daily closes and a theoretical 3-day path from the model's p_up_3d.

The model gives a probability of the price being higher in 3 days, not a price target. Read as a normal
distribution of the 3-day log return with the current daily volatility sigma, P(up) = p means the expected
3-day move is mu = sigma * sqrt(3) * inv_cdf(p). The band is +-1 sigma around that path (about 68% of
outcomes if the volatility holds). p = 0.5 gives a flat line: no view.
"""
import math
import time
from statistics import NormalDist

DAY_MS = 86400 * 1000
HISTORY_DAYS = 60
HORIZON_DAYS = 3


def sig(x):
    return float(f"{x:.6g}")


def forecast_path(price, p_up, daily_vol_pct, now_ms, days=HORIZON_DAYS):
    """Points [time_ms, expected, low, high] from now to `days` ahead, or [] without a forecast."""
    if p_up is None or not daily_vol_pct or price <= 0:
        return []
    sigma = daily_vol_pct / 100
    mu = sigma * math.sqrt(days) * NormalDist().inv_cdf(min(max(p_up, 0.02), 0.98))
    points = []
    for k in range(days + 1):
        mid = mu * k / days
        spread = sigma * math.sqrt(k)
        points.append([now_ms + k * DAY_MS, sig(price * math.exp(mid)),
                       sig(price * math.exp(mid - spread)), sig(price * math.exp(mid + spread))])
    return points


def chart(closed, price, signal, market, made):
    """What the daily cycle stores per pair (state["charts"]) and Home Assistant shows. It changes only once
    a day, so it gets its own entity: the recorder does not store a copy on every 15-minute refresh. The
    dashboard adds the current price from the pair entity."""
    now_ms = int(time.time() * 1000)
    vol = market.get("daily_volatility_pct") or market.get("atr14_pct")
    path = forecast_path(price, signal.get("p_up_3d"), vol, now_ms)
    out = {"history": [[(c["t"] + 86400) * 1000, sig(c["c"])] for c in closed[-HISTORY_DAYS:]],
           "forecast": path, "made": made, "p_up_3d": signal.get("p_up_3d")}
    if path:
        end = path[-1]
        out.update(expected_3d_pct=round((end[1] / price - 1) * 100, 2),
                   low_3d_pct=round((end[2] / price - 1) * 100, 2),
                   high_3d_pct=round((end[3] / price - 1) * 100, 2))
    return out
