"""Risk rules in code: they have the final word over the model."""
from datetime import datetime, timedelta, timezone

from . import exchange_stops, portfolio
from .i18n import t


def check_halt(cfg, state, prices):
    """Halts the whole portfolio after a large drop in equity. Returns the reason or None."""
    r = cfg["risk"]
    now = datetime.now(timezone.utc)
    if state.get("halted_until") and now.isoformat() < state["halted_until"]:
        return t("halted_until", date=state["halted_until"][:10])
    if portfolio.equity_of(state, prices) < state["start_eur"] * (1 - r["max_drawdown_pct"] / 100):
        state["halted_until"] = (now + timedelta(days=r["halt_days"])).isoformat()
        return t("halt_drawdown", dd=r["max_drawdown_pct"], days=r["halt_days"])
    return None


def stop_level(cfg, price, atr, model_stop):
    """Stop for a buy: the model's invalidation level (default 2 ATR below the price), but no closer than
    1 ATR (so ordinary noise does not shake us out) and no further than stop_loss_pct."""
    floor = price * (1 - cfg["risk"]["stop_loss_pct"] / 100)
    if not atr:
        return round(model_stop, 8) if model_stop and floor <= model_stop < price else floor
    stop = model_stop if model_stop and model_stop < price else price - 2 * atr
    return round(max(floor, min(stop, price - atr)), 8)


def tighten_stop(cfg, pos, signal, price, atr=None):
    """With an open position the model may raise the stop (never lower it). A position without a stop
    (coins bought outside the bot) gets its first stop within the same limits as a buy.
    Returns the new level or None."""
    new = signal.get("stop_price")
    if pos["coin"] <= 0 or not new or new >= price:
        return None
    if not pos.get("stop_price"):
        pos["stop_price"] = stop_level(cfg, price, atr, new)
        return pos["stop_price"]
    if pos["stop_price"] < new:
        pos["stop_price"] = round(new, 8)
        return pos["stop_price"]
    return None


def protective_exit(cfg, pos, price):
    """Stop-loss / take-profit: always active, regardless of the model and of a halt. With a stop order
    resting on Kraken the bot's own stop is only the backup: it acts when the price is well below that
    order's level, i.e. the exchange did not sell."""
    r = cfg["risk"]
    resting = pos.get("exchange_stop")
    if resting and pos["coin"] > 0 and price > resting["level"] * (1 - exchange_stops.GRACE_PCT / 100):
        if r.get("take_profit_pct") and pos["entry_price"] and \
                price >= pos["entry_price"] * (1 + r["take_profit_pct"] / 100):
            return {"side": "sell", "coin": pos["coin"], "why": "TAKE-PROFIT"}
        return None
    if pos["coin"] > 0 and pos.get("stop_price") and price <= pos["stop_price"]:
        return {"side": "sell", "coin": pos["coin"], "why": t("why_stop_model", level=pos["stop_price"])}
    if pos["coin"] > 0 and pos["entry_price"]:
        if price <= pos["entry_price"] * (1 - r["stop_loss_pct"] / 100):
            return {"side": "sell", "coin": pos["coin"], "why": "STOP-LOSS"}
        if r.get("take_profit_pct") and price >= pos["entry_price"] * (1 + r["take_profit_pct"] / 100):
            return {"side": "sell", "coin": pos["coin"], "why": "TAKE-PROFIT"}
    return None


def decide(cfg, state, pair, prices, signal, atr=None):
    """Turns the model's signal for one pair into an order. The rules in code have the final word."""
    r = cfg["risk"]
    price = prices[pair]
    pos = state["positions"][pair]

    if signal["action"] == "hold":
        return {"side": "none", "why": t("why_hold")}
    if signal["confidence"] < r["min_confidence"]:
        return {"side": "none", "why": t("why_low_conf", conf=signal["confidence"], min=r["min_confidence"])}

    if signal["action"] == "buy":
        equity = portfolio.equity_of(state, prices)
        total_exposure = equity - state["eur"]
        room_total = equity * r["max_exposure_pct"] / 100 - total_exposure
        per_pair_pct = r.get("max_exposure_per_pair_pct", r["max_exposure_pct"])
        room_pair = equity * per_pair_pct / 100 - pos["coin"] * price
        eur = min(state["eur"] * signal["size_pct"] / 100, r["max_trade_eur"],
                  room_total, room_pair, state["eur"])
        if eur < r["min_trade_eur"]:
            return {"side": "none", "why": t("why_buy_too_small")}
        stop = stop_level(cfg, price, atr, signal.get("stop_price"))
        return {"side": "buy", "eur": round(eur, 2), "stop_price": stop,
                "why": t("why_buy", stop=stop, pct=(stop / price - 1) * 100)}

    coin = pos["coin"] * signal["size_pct"] / 100
    if coin * price < r["min_trade_eur"]:
        return {"side": "none", "why": t("why_nothing_to_sell")}
    return {"side": "sell", "coin": coin, "why": t("why_sell")}
