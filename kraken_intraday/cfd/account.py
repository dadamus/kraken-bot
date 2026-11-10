"""The simulated CFD account (paper only).

A CFD here is a contract on an instrument's price, as at a CFD broker:
- a long opens at the ask and closes at the bid, a short the other way round; the mid price is Kraken's last
  trade and the spread (the whole spread in % of the price) is split half on each side,
- the margin is notional / leverage, set per instrument (EU retail limits: crypto 1:2, gold 1:20, major
  currency pairs 1:30),
- every open position pays the overnight swap (% of the notional, separately for long and short) once per
  calendar day it is held over midnight,
- when equity falls below stop_out_margin_level_pct of the margin used, the worst position is closed until
  the level is back above it (the broker's margin stop-out),
- an instrument with weekend hours (gold, currency pairs) cannot be traded and its stops do not fire while
  its market is closed; on the reopen a stop beyond the price fills at the first price (a gap).
The account is in EUR. Prices of a pair quoted in USD (EURUSD: USD per 1 EUR) are converted to EUR.
The balance holds realised P&L and paid swaps; equity adds the open positions valued at their closing price.

Per-instrument settings live in config.json "instruments" ({pair: {...}}); anything missing there falls
back to the top-level leverage, spread_pct and swap_pct_per_day.
"""
import json
import os
from datetime import datetime

from . import settings

FMT = "%Y-%m-%d %H:%M"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


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


# --- instruments ----------------------------------------------------------------------------------------

def instrument(cfg, pair):
    """The settings of one instrument: config.json "instruments"[pair] over the top-level defaults."""
    own = (cfg.get("instruments") or {}).get(pair) or {}
    s = cfg.get("spread_pct", 0)
    default_spread = float(s.get(pair, s.get("default", 0.5))) if isinstance(s, dict) else float(s)
    return {"name": own.get("name", pair), "asset_class": own.get("asset_class", "crypto"),
            "quote": own.get("quote", "EUR"), "leverage": float(own.get("leverage", cfg["leverage"])),
            "spread_pct": float(own.get("spread_pct", default_spread)),
            "swap_pct_per_day": {**cfg["swap_pct_per_day"], **(own.get("swap_pct_per_day") or {})},
            "weekend_close": own.get("weekend_close"), "weekend_open": own.get("weekend_open")}


def spread_pct(cfg, pair):
    return instrument(cfg, pair)["spread_pct"]


def leverage(cfg, pair):
    return instrument(cfg, pair)["leverage"]


def is_crypto(cfg, pair):
    return instrument(cfg, pair)["asset_class"] == "crypto"


def week_minute(text):
    """'fri 22:00' -> minutes since Monday 00:00."""
    day, hm = text.lower().split()
    h, m = hm.split(":")
    return DAYS.index(day[:3]) * 1440 + int(h) * 60 + int(m)


def market_open(cfg, pair, when):
    """False during the instrument's weekend break (local time), True otherwise and for 24/7 instruments."""
    inst = instrument(cfg, pair)
    if not inst["weekend_close"] or not inst["weekend_open"]:
        return True
    close, reopen = week_minute(inst["weekend_close"]), week_minute(inst["weekend_open"])
    now = when.weekday() * 1440 + when.hour * 60 + when.minute
    closed = close <= now < reopen if close < reopen else (now >= close or now < reopen)
    return not closed


def eur_per_quote(cfg, pair, mid, prices=None, pos=None):
    """EUR for 1 unit of the pair's quote currency (1 for EUR pairs)."""
    quote = instrument(cfg, pair)["quote"]
    if quote == "EUR":
        return 1.0
    if quote != "USD":
        raise ValueError(f"{pair}: quote currency {quote} is not supported (EUR or USD)")
    if pair == "EURUSD":
        return 1 / mid
    rate = (prices or {}).get("EURUSD")
    if rate:
        return 1 / rate
    if pos and pos.get("fx"):
        return pos["fx"]
    raise RuntimeError(f"{pair}: the EURUSD rate is needed to value it in EUR")


# --- prices and P&L -------------------------------------------------------------------------------------

def fill(cfg, pair, mid, side, opening):
    """The price a market order gets: opening a long or closing a short buys at the ask, the rest sells at
    the bid."""
    half = spread_pct(cfg, pair) / 200
    buying = (side == "long") == opening
    return mid * (1 + half) if buying else mid * (1 - half)


def pnl(pos, exit_price):
    """P&L in the pair's quote currency."""
    move = exit_price - pos["entry"]
    return pos["qty"] * (move if pos["side"] == "long" else -move)


def price_of(prices, pair, pos):
    return prices.get(pair) or pos.get("last_price") or pos["entry_mid"]


def unrealized(cfg, pair, pos, mid, prices=None):
    """P&L in EUR if the position were closed now (at the bid for a long, the ask for a short)."""
    return pnl(pos, fill(cfg, pair, mid, pos["side"], False)) * eur_per_quote(cfg, pair, mid, prices, pos)


def notional_eur(cfg, pair, qty, mid, prices=None, pos=None):
    return qty * mid * eur_per_quote(cfg, pair, mid, prices, pos)


def equity(cfg, state, prices):
    return state["balance"] + sum(unrealized(cfg, pair, pos, price_of(prices, pair, pos), prices)
                                  for pair, pos in state["positions"].items())


def margin_used(cfg, state, prices):
    return sum(notional_eur(cfg, pair, pos["qty"], price_of(prices, pair, pos), prices, pos) / leverage(cfg, pair)
               for pair, pos in state["positions"].items())


def margin_level(cfg, state, prices):
    """Equity as % of the margin used (None without positions)."""
    used = margin_used(cfg, state, prices)
    return equity(cfg, state, prices) / used * 100 if used else None


# --- orders ---------------------------------------------------------------------------------------------

def open_position(cfg, state, pair, side, qty, mid, stop, take_profit, now, prices=None):
    entry = fill(cfg, pair, mid, side, True)
    fx = eur_per_quote(cfg, pair, mid, prices)
    spread = qty * abs(entry - mid) * fx
    pos = {"side": side, "qty": qty, "entry": entry, "entry_mid": mid, "stop": stop, "initial_stop": stop,
           "take_profit": take_profit, "opened": now.strftime(FMT), "opened_ts": int(now.timestamp()),
           "swap_date": now.strftime("%Y-%m-%d"), "swap_eur": 0.0, "spread_eur": spread, "last_price": mid,
           "fx": fx}
    state["positions"][pair] = pos
    costs = state.setdefault("costs", {"spread_eur": 0.0, "swap_eur": 0.0})
    costs["spread_eur"] = costs.get("spread_eur", 0.0) + spread
    return pos


def close_position(cfg, state, pair, mid, reason, now, prices=None):
    """Closes the whole position at `mid` (plus half the spread). Returns the trades.csv row."""
    pos = state["positions"].pop(pair)
    fx = eur_per_quote(cfg, pair, mid, prices, pos)
    exit_price = fill(cfg, pair, mid, pos["side"], False)
    spread_exit = pos["qty"] * abs(mid - exit_price) * fx
    gross = pnl(pos, exit_price) * fx  # the spread on both sides is already in the fill prices
    state["balance"] += gross
    costs = state.setdefault("costs", {"spread_eur": 0.0, "swap_eur": 0.0})
    costs["spread_eur"] = costs.get("spread_eur", 0.0) + spread_exit
    net = gross - pos["swap_eur"]  # the swap was taken from the balance day by day
    risk_eur = pos["qty"] * abs(pos["entry_mid"] - pos["initial_stop"]) * fx if pos.get("initial_stop") else 0
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
        rate = float(instrument(cfg, pair)["swap_pct_per_day"][pos["side"]])
        fee = notional_eur(cfg, pair, pos["qty"], price_of(prices, pair, pos), prices, pos) * rate / 100 * days
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
    with the biggest loss among those whose market is open is closed. Returns the trades.csv rows."""
    limit = float(cfg.get("stop_out_margin_level_pct", 50))
    rows = []
    while state["positions"]:
        level = margin_level(cfg, state, prices)
        if level is None or level >= limit:
            break
        tradable = [p for p in state["positions"] if market_open(cfg, p, now)]
        if not tradable:
            break
        worst = min(tradable, key=lambda p: unrealized(cfg, p, state["positions"][p],
                                                      price_of(prices, p, state["positions"][p]), prices))
        pos = state["positions"][worst]
        rows.append(close_position(cfg, state, worst, price_of(prices, worst, pos), "STOP-OUT", now, prices))
    return rows
