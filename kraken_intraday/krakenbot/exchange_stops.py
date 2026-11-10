"""Stop-loss orders resting on Kraken, so a position is protected even when Home Assistant, the add-on or
the internet is down. Live mode only (not in validation mode).

Every refresh and cycle reconciles what should rest on the exchange with what does:
- wanted: one stop-loss sell for the whole position at the higher of the model's stop and the fixed
  stop_loss_pct below the entry price (the level at which the bot itself would sell),
- the bot's orders carry USERREF, so orders placed by hand are never touched,
- a different level or volume -> the old order is cancelled and a new one placed,
- an order that disappeared and was filled -> a "stop" row in the log, like a stop sold by the bot.
The position keeps {"txid", "level", "volume"} in pos["exchange_stop"]. Take-profit stays with the bot.
"""
import math
from datetime import datetime

from . import alerts, kraken, portfolio
from .i18n import t

USERREF = 73101  # marks the bot's own stop orders
GRACE_PCT = 1.0  # the bot's own stop check acts only this far below a resting exchange stop


def enabled(cfg, live):
    return live and not cfg.get("live_validate_only", True) and cfg["risk"].get("exchange_stops", True)


def wanted_level(cfg, pos):
    levels = [pos["stop_price"]] if pos.get("stop_price") else []
    if pos.get("entry_price"):
        levels.append(pos["entry_price"] * (1 - cfg["risk"]["stop_loss_pct"] / 100))
    return max(levels) if levels else None


def wanted_order(cfg, pos, info, price):
    """(level, volume) the exchange order should have, or None (no position, no level, too small, or the
    price is already below the level - then the bot sells by itself)."""
    level = wanted_level(cfg, pos)
    if not pos.get("coin") or not level or level >= price:
        return None
    dec = info["lot_decimals"]
    volume = math.floor(pos["coin"] * 10 ** dec) / 10 ** dec
    if volume < info["ordermin"] or volume * price < cfg["risk"]["min_trade_eur"]:
        return None
    return round(level, info.get("pair_decimals", 8)), volume


def open_orders():
    """The bot's open orders: [(txid, order)]."""
    res = kraken.kraken_private("/0/private/OpenOrders", {"userref": USERREF})
    return list((res.get("open") or {}).items())


def cancel(txid):
    try:
        kraken.kraken_private("/0/private/CancelOrder", {"txid": txid})
        return True
    except Exception as e:
        print(t("exchange_stop_cancel_failed", txid=txid, err=e))
        return False


def cancel_for_sale(cfg, live, pos):
    """Before the bot sells a position itself: the resting stop would keep the coins reserved."""
    stop = pos.get("exchange_stop")
    if enabled(cfg, live) and stop:
        cancel(stop["txid"])
        pos["exchange_stop"] = None


def place(info, level, volume):
    dec = info.get("pair_decimals", 8)
    res = kraken.kraken_private("/0/private/AddOrder", {
        "pair": info["key"], "type": "sell", "ordertype": "stop-loss", "price": f"{level:.{dec}f}",
        "volume": f"{volume:.{info['lot_decimals']}f}", "userref": USERREF})
    return res["txid"][0]


def matches(order, level, volume, info):
    tick = 10 ** -info.get("pair_decimals", 8)
    lot = 10 ** -info["lot_decimals"]
    return (order["descr"].get("ordertype") == "stop-loss" and order["descr"].get("type") == "sell"
            and abs(float(order["descr"]["price"]) - level) < tick / 2 and abs(float(order["vol"]) - volume) < lot / 2)


def filled_row(pair, stop, done, state, prices):
    price = float(done.get("price") or 0) or stop["level"]
    return {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "pair": pair, "price": round(price, 6),
            "model_action": "stop", "confidence": 0.0, "p_up_3d": None, "size_pct": 100, "executed": "sell",
            "rule": t("why_exchange_stop", level=stop["level"]), "qty": round(float(done.get("vol_exec") or 0), 8),
            "eur": round(state["eur"], 2), "coin": round(state["positions"][pair]["coin"], 8),
            "equity_eur": round(portfolio.equity_of(state, prices), 2), "reasoning": t("exchange_stop_reasoning"),
            "cycle": "guard"}


def sync(cfg, state, info, prices, live=True):
    """Reconciles the exchange stops with the positions (after sync_live). Returns log rows for stops
    that were filled on the exchange. Errors are printed, never raised: the bot's own check still works."""
    if not enabled(cfg, live) or not info:
        return []
    rows = []
    try:
        resting = open_orders()
        for pair, i in info.items():
            pos = state["positions"][pair]
            known = pos.get("exchange_stop")
            # by pair name, and the order we placed by its txid (whatever pair name Kraken reports)
            mine = [(txid, o) for txid, o in resting
                    if o["descr"].get("pair") in (pair, i["key"]) or (known and txid == known["txid"])]
            if known and known["txid"] not in {txid for txid, _ in mine}:
                done = kraken.kraken_private("/0/private/QueryOrders", {"txid": known["txid"]}).get(known["txid"], {})
                if done.get("status") in ("open", "pending"):  # not in the listing, but still resting
                    raise RuntimeError(f"order {known['txid']} is {done['status']} but not listed")
                if done.get("status") == "closed" and float(done.get("vol_exec") or 0) > 0:
                    print(t("exchange_stop_filled", pair=pair, level=known["level"], price=done.get("price")))
                    rows.append(filled_row(pair, known, done, state, prices))
                pos["exchange_stop"] = known = None
            want = wanted_order(cfg, pos, i, prices[pair])
            keep = None
            for txid, o in mine:
                if keep is None and want and matches(o, *want, i):
                    keep = txid
                elif cancel(txid):
                    print(t("exchange_stop_cancelled", pair=pair, price=o["descr"].get("price")))
            if keep:
                pos["exchange_stop"] = {"txid": keep, "level": want[0], "volume": want[1]}
            elif want:
                txid = place(i, *want)
                pos["exchange_stop"] = {"txid": txid, "level": want[0], "volume": want[1]}
                print(t("exchange_stop_placed", pair=pair, level=want[0], volume=want[1]))
            else:
                pos["exchange_stop"] = None
        alerts.problem(state, "exchange_stop")
    except Exception as e:
        print(t("exchange_stop_failed", err=e))
        alerts.problem(state, "exchange_stop", t("alert_exchange_stop", err=str(e)[:300]))
    return rows
