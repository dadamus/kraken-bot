"""The rules in code. The model proposes a direction; these decide whether, how big and with which stop.

- a new position needs confidence >= min_confidence; closing (flat, or a call for the other direction) does
  not, because it only removes risk,
- every position has a stop and a take-profit: the model's levels when they are on the right side, the stop
  kept between min_stop_atr daily ATRs and max_stop_pct from the price (default default_stop_atr ATRs), the
  take-profit at least 1x the stop distance (default default_reward_risk x),
- the size risks risk_per_trade_pct of equity between the entry and the stop, capped by the margin limits
  (max_margin_per_trade_pct for one position, max_margin_pct for all) and skipped below min_trade_eur,
- a drawdown of max_drawdown_pct from the peak equity stops new positions for halt_days,
- the model may move a stop closer (never further), the take-profit stays,
- within event_window_hours before a high-impact US release (CPI, FOMC, payrolls...) the stop is at least
  event_stop_atr ATRs away, for new positions and for a stop the model moves closer: the noise before and
  right after the release takes out tight stops. The risk in EUR stays risk_per_trade_pct, so the position
  is smaller.
"""
from datetime import datetime, timedelta, timezone

from krakenbot import macro

from . import account

FMT = "%Y-%m-%d %H:%M"


def sig(x):
    return float(f"{x:.6g}")


def event_ahead(cfg, state, now):
    """The next high-impact US release within event_window_hours from the cached macro calendar, or None.
    `now`: naive local time (as in the cycle) or aware."""
    hours = float(cfg["risk"].get("event_window_hours", 0) or 0)
    cache = state.get("macro_calendar_cache") or {}
    if hours <= 0 or not cache.get("events"):
        return None
    now_utc = now.astimezone(timezone.utc)
    ahead = [e for e in macro.calendar_events(cache["events"], now_utc, past_hours=0) if e["in_hours"] <= hours]
    return ahead[0] if ahead else None


def min_stop_atr(cfg, event=None):
    """The minimum stop distance in daily ATRs: wider while a high-impact release is ahead."""
    r = cfg["risk"]
    return max(r["min_stop_atr"], r.get("event_stop_atr", 0) or 0) if event else r["min_stop_atr"]


def levels(cfg, side, price, atr, model_stop=None, model_tp=None, event=None):
    """(stop, take_profit, stop distance) for a new position."""
    r = cfg["risk"]
    unit = atr or price * 0.03  # no ATR yet (short history): assume a 3 % daily range
    sign = 1 if side == "long" else -1
    dist = abs(price - model_stop) if model_stop and (price - model_stop) * sign > 0 else unit * r["default_stop_atr"]
    dist = min(max(dist, unit * min_stop_atr(cfg, event)), price * r["max_stop_pct"] / 100)  # the cap wins
    if model_tp and (model_tp - price) * sign >= dist:
        tp = model_tp
    else:
        tp = price + sign * dist * r["default_reward_risk"]
    return sig(price - sign * dist), sig(tp), dist


def size(cfg, state, prices, pair, price, dist):
    """Quantity in units of the instrument (coins, ounces, euros), or (0, reason). `dist` is in the pair's
    quote currency; risk and margin are in EUR."""
    r = cfg["risk"]
    lev = account.leverage(cfg, pair)
    fx = account.eur_per_quote(cfg, pair, price, prices)
    eq = account.equity(cfg, state, prices)
    qty = eq * r["risk_per_trade_pct"] / 100 / (dist * fx)
    room = min(eq * r["max_margin_per_trade_pct"] / 100, eq * r["max_margin_pct"] / 100
               - account.margin_used(cfg, state, prices))
    qty = min(qty, max(room, 0) * lev / (price * fx))
    notional = qty * price * fx
    if notional < r["min_trade_eur"]:
        return 0.0, f"size {notional:.0f} EUR below min_trade_eur (margin room {max(room, 0):.0f} EUR)"
    return qty, None


def check_halt(cfg, state, prices, now):
    """The drawdown halt. Keeps the peak equity up to date; returns the reason while halted, else None."""
    eq = account.equity(cfg, state, prices)
    state["peak_equity"] = max(state.get("peak_equity") or eq, eq)
    until = state.get("halted_until")
    if until and now < datetime.strptime(until, FMT):
        return f"drawdown halt until {until}"
    state["halted_until"] = None
    drawdown = (1 - eq / state["peak_equity"]) * 100
    if drawdown >= cfg["risk"]["max_drawdown_pct"]:
        state["halted_until"] = (now + timedelta(days=cfg["risk"]["halt_days"])).strftime(FMT)
        state["peak_equity"] = eq  # after the halt the drawdown is measured afresh
        return f"drawdown {drawdown:.1f}% -> no new positions until {state['halted_until']}"
    return None


def tighten(pos, model_stop, price, min_dist=0.0):
    """Moves the stop closer to the price when the model gives a tighter one, but not within min_dist of the
    price (the release floor). Returns the new stop or None."""
    if not model_stop:
        return None
    if min_dist:
        sign = 1 if pos["side"] == "long" else -1
        model_stop = min(model_stop, price - min_dist) if sign == 1 else max(model_stop, price + min_dist)
    if pos["side"] == "long" and pos["stop"] < model_stop < price:
        pos["stop"] = sig(model_stop)
        return pos["stop"]
    if pos["side"] == "short" and price < model_stop < pos["stop"]:
        pos["stop"] = sig(model_stop)
        return pos["stop"]
    return None


def decide(cfg, state, pair, price, sig_, atr, halt, event=None):
    """The steps for one pair, in order: {"do": "close" | "open" | "none", "why": ...}; an "open" step carries
    side, stop, take_profit and dist (the size is computed when it runs, after the closes freed margin)."""
    pos = state["positions"].get(pair)
    action, conf = sig_["action"], sig_["confidence"]
    note = ""
    if pos and action in ("hold", pos["side"]):
        floor = (atr or price * 0.03) * min_stop_atr(cfg, event) if event else 0.0
        raised = tighten(pos, sig_.get("stop_price"), price, floor)
        note = f", stop moved to {raised:g}" if raised else ""
    if action == "hold":
        return [{"do": "none", "why": f"model: hold{note}"}]
    if action == "flat":
        return [{"do": "close", "why": "model: flat"}] if pos else [{"do": "none", "why": "model: flat"}]
    if pos and pos["side"] == action:
        return [{"do": "none", "why": f"model: {action}, already {action}{note}"}]
    steps = [{"do": "close", "why": f"model: {action}, closes the {pos['side']}"}] if pos else []
    min_conf = cfg["risk"]["min_confidence"]
    if action == "short" and not cfg.get("allow_short", True):
        steps.append({"do": "none", "why": "model: short, shorts are off (allow_short)"})
    elif conf < min_conf:
        steps.append({"do": "none", "why": f"model: {action}, confidence {conf:.2f} < {min_conf}"})
    elif halt:
        steps.append({"do": "none", "why": f"model: {action}, {halt}"})
    else:
        stop, tp, dist = levels(cfg, action, price, atr, sig_.get("stop_price"), sig_.get("take_profit"), event)
        wide = f" (min {min_stop_atr(cfg, event):g} ATR before {event['event']})" if event else ""
        steps.append({"do": "open", "side": action, "stop": stop, "take_profit": tp, "dist": dist,
                      "why": f"model: {action}, stop {stop:g}{wide}, take-profit {tp:g}"})
    return steps
