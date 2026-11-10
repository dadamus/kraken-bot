"""Extra model cycles between the daily ones, started by events instead of the clock.

The refresh (every refresh_minutes, never asks the model itself) checks three events since the last cycle:
- a stop / take-profit fired at least after_stop_hours ago - was it a real break or just a wick?
- the price crossed a level the model gave in its last call (watch: "my call could change above / below X"),
- the price of a pair moved at least move_atr daily ATRs from the price of the last cycle.
Limits: at least min_gap_hours since the last cycle of any kind, at most max_per_day extra cycles a day.
"""
from datetime import datetime, timedelta

from .i18n import t

FMT = "%Y-%m-%d %H:%M"
DEFAULTS = {"enabled": True, "after_stop_hours": 3, "move_atr": 1.5, "watch_levels": True, "min_gap_hours": 3,
            "max_per_day": 3}


def settings(cfg):
    return {**DEFAULTS, **(cfg.get("extra_cycles") or {})}


def watch_levels(signals, prices):
    """The model's watch per pair. A level on the wrong side of the price (already crossed) is dropped, so it
    cannot start an extra cycle at once."""
    out = {}
    for pair, sig in (signals or {}).items():
        w, price = sig.get("watch") or {}, prices.get(pair)
        above = w.get("above") if w.get("above") and price and w["above"] > price else None
        below = w.get("below") if w.get("below") and price and w["below"] < price else None
        if above or below or w.get("condition"):
            out[pair] = {"above": above, "below": below, "condition": w.get("condition") or ""}
    return out


def remember_cycle(state, markets, prices, now=None, signals=None):
    """Reference point for the next check: when the model was last asked, at what prices and which levels
    it wanted to be asked again at."""
    state["last_cycle"] = {"date": (now or datetime.now()).strftime(FMT),
                           "prices": {pair: round(price, 8) for pair, price in prices.items()},
                           "atr": {pair: markets[pair].get("atr14") for pair in prices},
                           "watch": watch_levels(signals, prices)}


def watch_hit(last, pair, price):
    """("above" | "below", level) when the price crossed a level from the model's watch, else None."""
    w = (last.get("watch") or {}).get(pair) or {}
    if w.get("above") and price >= w["above"]:
        return "above", w["above"]
    if w.get("below") and price <= w["below"]:
        return "below", w["below"]
    return None


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
        hit = s["watch_levels"] and watch_hit(last, pair, price)
        if hit:
            return {"kind": "watch", "pair": pair, "direction": hit[0], "level": hit[1],
                    "condition": last["watch"][pair]["condition"], "since": last["date"],
                    "price_then": last["prices"].get(pair), "price_now": price}
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
    if reason["kind"] == "watch":
        return t(f"trigger_watch_{reason['direction']}", pair=reason["pair"], b=reason["price_now"],
                 level=reason["level"], cond=reason["condition"] or "-")
    return t("trigger_move", pair=reason["pair"], since=reason["since"], a=reason["price_then"],
             b=reason["price_now"], pct=reason["change_pct"], atr=reason["atr_multiple"])


def cycle_info(reason):
    """What the model is told about why it is being asked now."""
    return {"type": "scheduled_daily"} if not reason else {"type": "extra", "trigger": reason}
