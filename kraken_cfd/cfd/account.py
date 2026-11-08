"""The simulated CFD account (paper only).

A CFD here is a contract on the coin's EUR price, as at a CFD broker:
- a long opens at the ask and closes at the bid, a short the other way round; the mid price is Kraken's last
  trade and the spread (spread_pct, the whole spread in % of the price) is split half on each side,
- the margin is notional / leverage (2:1 is the EU retail limit for crypto CFDs),
- every open position pays the overnight swap (swap_pct_per_day of the notional, separately for long and
  short) once per calendar day it is held over midnight,
- when equity falls below stop_out_margin_level_pct of the margin used, the worst position is closed until
  the level is back above it (the broker's margin stop-out).
The balance holds realised P&L and paid swaps; equity adds the open positions valued at their closing price.
"""
import json
import os
from datetime import datetime

from . import settings

FMT = "%Y-%m-%d %H:%M"


def new_state(cfg, now=None):
    now = now or datetime.now()
    start = float(cfg["start_eur"])
    return {"balance": start, "start_eur": start, "positions": {}, "peak_equity": start, "halted_until": None,
            "first_prices": {}, "started": now.strftime(FMT), "last_check": None,
            "costs": {"spread_eur": 0.0, "swap_eur": 0.0}}


def load_state(cfg):
    try:
        return json.loads(settings.STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return new_state(cfg)


def save_state(state):
    path = settings.STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # never a half-written state


def spread_pct(cfg, pair):
    s = cfg.get("spread_pct", 0)
    if isinstance(s, dict):
        return float(s.get(pair, s.get("default", 0.5)))
    return float(s)


def fill(cfg, pair, mid, side, opening):
    """The price a market order gets: opening a long or closing a short buys at the ask, the rest sells at
    the bid."""
    half = spread_pct(cfg, pair) / 200
    buying = (side == "long") == opening
    return mid * (1 + half) if buying else mid * (1 - half)


def pnl(pos, exit_price):
    move = exit_price - pos["entry"]
    return pos["qty"] * (move if pos["side"] == "long" else -move)


def price_of(prices, pair, pos):
    return prices.get(pair) or pos.get("last_price") or pos["entry_mid"]


def unrealized(cfg, pair, pos, mid):
    """P&L if the position were closed now (at the bid for a long, the ask for a short)."""
    return pnl(pos, fill(cfg, pair, mid, pos["side"], False))


def equity(cfg, state, prices):
    return state["balance"] + sum(unrealized(cfg, pair, pos, price_of(prices, pair, pos))
                                  for pair, pos in state["positions"].items())


def margin_used(cfg, state, prices):
    lev = float(cfg["leverage"])
    return sum(pos["qty"] * price_of(prices, pair, pos) / lev for pair, pos in state["positions"].items())


def margin_level(cfg, state, prices):
    """Equity as % of the margin used (None without positions)."""
    used = margin_used(cfg, state, prices)
    return equity(cfg, state, prices) / used * 100 if used else None


def open_position(cfg, state, pair, side, qty, mid, stop, take_profit, now):
    entry = fill(cfg, pair, mid, side, True)
    spread = qty * abs(entry - mid)
    pos = {"side": side, "qty": qty, "entry": entry, "entry_mid": mid, "stop": stop, "initial_stop": stop,
           "take_profit": take_profit, "opened": now.strftime(FMT), "opened_ts": int(now.timestamp()),
           "swap_date": now.strftime("%Y-%m-%d"), "swap_eur": 0.0, "spread_eur": spread, "last_price": mid}
    state["positions"][pair] = pos
    costs = state.setdefault("costs", {"spread_eur": 0.0, "swap_eur": 0.0})
    costs["spread_eur"] = costs.get("spread_eur", 0.0) + spread
    return pos


def close_position(cfg, state, pair, mid, reason, now):
    """Closes the whole position at `mid` (plus half the spread). Returns the trades.csv row."""
    pos = state["positions"].pop(pair)
    exit_price = fill(cfg, pair, mid, pos["side"], False)
    spread_exit = pos["qty"] * abs(mid - exit_price)
    gross = pnl(pos, exit_price)  # the spread on both sides is already in the fill prices
    state["balance"] += gross
    costs = state.setdefault("costs", {"spread_eur": 0.0, "swap_eur": 0.0})
    costs["spread_eur"] = costs.get("spread_eur", 0.0) + spread_exit
    net = gross - pos["swap_eur"]  # the swap was taken from the balance day by day
    risk_eur = pos["qty"] * abs(pos["entry_mid"] - pos["initial_stop"]) if pos.get("initial_stop") else 0
    sign = 1 if pos["side"] == "long" else -1
    opened = datetime.strptime(pos["opened"], FMT)
    return {"opened": pos["opened"], "closed": now.strftime(FMT), "pair": pair, "side": pos["side"],
            "qty": round(pos["qty"], 8), "entry": round(pos["entry"], 6), "exit": round(exit_price, 6),
            "stop": pos.get("stop"), "take_profit": pos.get("take_profit"), "reason": reason,
            "move_pct": round((mid / pos["entry_mid"] - 1) * 100 * sign, 2),
            "pnl_eur": round(gross, 2), "swap_eur": round(pos["swap_eur"], 2),
            "spread_eur": round(pos["spread_eur"] + spread_exit, 2), "net_eur": round(net, 2),
            "r_multiple": round(net / risk_eur, 2) if risk_eur else None,
            "days": round(max(0.0, (now - opened).total_seconds()) / 86400, 2),
            "balance_eur": round(state["balance"], 2)}


def charge_swaps(cfg, state, prices, now):
    """The overnight swap for every midnight a position was held since the last charge. Returns the total."""
    today, total = now.date(), 0.0
    for pair, pos in state["positions"].items():
        days = (today - datetime.strptime(pos["swap_date"], "%Y-%m-%d").date()).days
        if days <= 0:
            continue
        rate = float(cfg["swap_pct_per_day"][pos["side"]])
        fee = pos["qty"] * price_of(prices, pair, pos) * rate / 100 * days
        pos["swap_eur"] += fee
        pos["swap_date"] = today.isoformat()
        state["balance"] -= fee
        total += fee
    if total:
        costs = state.setdefault("costs", {"spread_eur": 0.0, "swap_eur": 0.0})
        costs["swap_eur"] = costs.get("swap_eur", 0.0) + total
    return total


def stop_out(cfg, state, prices, now):
    """The broker's margin stop-out: while equity is below the threshold of the margin used, the position
    with the biggest loss is closed. Returns the trades.csv rows."""
    limit = float(cfg.get("stop_out_margin_level_pct", 50))
    rows = []
    while state["positions"]:
        level = margin_level(cfg, state, prices)
        if level is None or level >= limit:
            break
        worst = min(state["positions"], key=lambda p: unrealized(cfg, p, state["positions"][p],
                                                                 price_of(prices, p, state["positions"][p])))
        pos = state["positions"][worst]
        rows.append(close_position(cfg, state, worst, price_of(prices, worst, pos), "STOP-OUT", now))
    return rows
