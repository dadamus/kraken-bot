"""Portfolio state (state.json / state_live.json), equity and simulated fills."""
import json

from . import config


def empty_position():
    return {"coin": 0.0, "entry_price": None}


def load_state(cfg):
    if config.STATE_FILE.exists():
        state = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
    else:
        state = {"eur": cfg["paper_start_eur"], "start_eur": cfg["paper_start_eur"],
                 "positions": {}, "first_prices": {}, "halted_until": None}
    if "positions" not in state:  # migration from the single-pair state
        old_pair = cfg.get("pair", cfg["pairs"][0])
        state["positions"] = {old_pair: {"coin": state.pop("coin", 0.0),
                                         "entry_price": state.pop("entry_price", None)}}
        fp = state.pop("first_price", None)
        state["first_prices"] = {old_pair: fp} if fp else {}
    for pair in cfg["pairs"]:
        state["positions"].setdefault(pair, empty_position())
    return state


def save_state(state):
    config.STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def equity_of(state, prices):
    return state["eur"] + sum(p["coin"] * prices[pair] for pair, p in state["positions"].items()
                              if pair in prices)


def hold_value(hold, prices):
    """Value of the "do nothing" portfolio: what the account held at the start, plus deposits as they came."""
    if any(p not in prices or prices[p] is None for p, q in hold["coins"].items() if q):
        return None
    return hold["eur"] + sum(q * prices[p] for p, q in hold["coins"].items() if q)


def vs_hold(state, prices):
    """The bot against simply keeping the starting holdings, over the same period and with the same
    deposits and withdrawals: {"since", "value", "return_pct", "diff_eur", "diff_pp"} or None."""
    hold, start = state.get("hold_start"), state.get("start_eur")
    value = hold_value(hold, prices) if hold else None
    if value is None or not start or any(prices.get(p) is None for p, pos in state["positions"].items() if pos.get("coin")):
        return None
    diff = equity_of(state, {p: v for p, v in prices.items() if v is not None}) - value
    return {"since": hold["since"], "value": round(value, 2), "return_pct": round((value / start - 1) * 100, 2),
            "diff_eur": round(diff, 2), "diff_pp": round(diff / start * 100, 2)}


def paper_execute(cfg, state, pair, order, price):
    """Simulated fill with fee and slippage. In live mode it updates the working copy of the state."""
    fee = cfg["fee_pct"] / 100
    slip = cfg.get("slippage_pct", 0.1) / 100
    pos = state["positions"][pair]
    if order["side"] == "buy":
        fill = price * (1 + slip)
        coin = order["eur"] * (1 - fee) / fill
        old_cost = (pos["entry_price"] or 0) * pos["coin"]
        state["eur"] -= order["eur"]
        pos["coin"] += coin
        pos["entry_price"] = (old_cost + order["eur"]) / pos["coin"]
        if order.get("stop_price"):  # adding to a position: the stop of the latest thesis applies
            pos["stop_price"] = order["stop_price"]
        pos["peak_price"] = max(pos.get("peak_price") or fill, fill)
        return fill, coin
    if order["side"] == "sell":
        fill = price * (1 - slip)
        coin = min(order["coin"], pos["coin"])
        state["eur"] += coin * fill * (1 - fee)
        pos["coin"] -= coin
        if pos["coin"] < 1e-9:
            pos["coin"], pos["entry_price"], pos["stop_price"] = 0.0, None, None
            pos["peak_price"] = None
        return fill, coin
    return None, 0.0
