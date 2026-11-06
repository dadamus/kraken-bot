"""Extra model cycles between the daily ones, started by events instead of the clock.

The refresh (every refresh_minutes, never asks the model itself) checks two events since the last cycle:
- a stop / take-profit fired at least after_stop_hours ago - was it a real break or just a wick?
- the price of a pair moved at least move_atr daily ATRs from the price of the last cycle.
Limits: at least min_gap_hours since the last cycle of any kind, at most max_per_day extra cycles a day.
"""
from datetime import datetime, timedelta

from .i18n import t

FMT = "%Y-%m-%d %H:%M"
DEFAULTS = {"enabled": True, "after_stop_hours": 3, "move_atr": 1.5, "min_gap_hours": 3, "max_per_day": 3}


def settings(cfg):
    return {**DEFAULTS, **(cfg.get("extra_cycles") or {})}


def remember_cycle(state, markets, prices, now=None):
    """Reference point for the next check: when the model was last asked and at what prices."""
    state["last_cycle"] = {"date": (now or datetime.now()).strftime(FMT),
                           "prices": {pair: round(price, 8) for pair, price in prices.items()},
                           "atr": {pair: markets[pair].get("atr14") for pair in prices}}


def extra_cycle_reason(cfg, state, prices, log, now=None):
    """The event that warrants an extra cycle now, or None. `log` = journal.read_log()."""
    s, now = settings(cfg), now or datetime.now()
    last = state.get("last_cycle")
    if not s["enabled"] or not last:
        return None
    if now - datetime.strptime(last["date"], FMT) < timedelta(hours=s["min_gap_hours"]):
        return None
    done = state.get("extra_cycles") or {}
    if done.get("date") == now.strftime("%Y-%m-%d") and done.get("n", 0) >= s["max_per_day"]:
        return None
    for r in log:
        if r["model_action"] == "stop" and r["executed"] == "sell" and r["date"] > last["date"] \
                and now - datetime.strptime(r["date"], FMT) >= timedelta(hours=s["after_stop_hours"]):
            return {"kind": "stop", "pair": r["pair"], "rule": r["rule"], "at": r["date"],
                    "price_then": float(r["price"]), "price_now": prices.get(r["pair"])}
    for pair, price in prices.items():
        ref, atr = last["prices"].get(pair), (last.get("atr") or {}).get(pair)
        if ref and atr and abs(price - ref) >= s["move_atr"] * atr:
            return {"kind": "move", "pair": pair, "since": last["date"], "price_then": ref, "price_now": price,
                    "change_pct": round((price / ref - 1) * 100, 2), "atr_multiple": round(abs(price - ref) / atr, 2)}
    return None


def count_extra(state, now=None):
    """Counted before the cycle runs: a failing cycle is not retried on every refresh."""
    today = (now or datetime.now()).strftime("%Y-%m-%d")
    done = state.get("extra_cycles") or {}
    state["extra_cycles"] = {"date": today, "n": (done.get("n", 0) if done.get("date") == today else 0) + 1}


def describe(reason):
    if reason["kind"] == "stop":
        return t("trigger_stop", pair=reason["pair"], rule=reason["rule"], at=reason["at"])
    return t("trigger_move", pair=reason["pair"], since=reason["since"], a=reason["price_then"],
             b=reason["price_now"], pct=reason["change_pct"], atr=reason["atr_multiple"])


def cycle_info(reason):
    """What the model is told about why it is being asked now."""
    return {"type": "scheduled_daily"} if not reason else {"type": "extra", "trigger": reason}
