"""Protection between cycles: stop-loss / take-profit on the current price, no model."""
import copy
import time
from datetime import datetime

from . import kraken, portfolio, risk
from .i18n import t


def guard(cfg, state, prices, live, pair_info=None):
    """Protection on every refresh (--ha, in the add-on every refresh_minutes): stop-loss and take-profit
    on the current price. No model - just API prices. Returns rows for the log."""
    if not cfg["risk"].get("intraday_stops", True):
        return []
    validate = live and cfg.get("live_validate_only", True)
    today = datetime.now().strftime("%Y-%m-%d")
    fired = state.setdefault("guard_fired", {})
    book = copy.deepcopy(state) if live else state  # in live the real balance is read at the end anyway
    rows, now_s = [], datetime.now().strftime("%Y-%m-%d %H:%M")
    for pair, price in prices.items():
        pos = book["positions"].get(pair)
        order = risk.protective_exit(cfg, pos, price) if pos else None
        # in validation mode the position stays, so without this the same stop would fire every 15 minutes
        if not order or (validate and fired.get(pair) == today):
            continue
        print(t("guard_trigger", pair=pair, why=order["why"], price=price))
        qty = 0.0
        if live and not kraken.live_execute(cfg, pair_info[pair], order, price):
            order = {"side": "error", "why": t("why_order_failed", why=order["why"])}
        else:
            if validate:
                order["why"] += t("why_validate_sell")
            _, qty = portfolio.paper_execute(cfg, book, pair, order, price)
            fired[pair] = today
        rows.append({"date": now_s, "pair": pair, "price": round(price, 6), "model_action": "stop",
                     "confidence": 0.0, "p_up_3d": None, "size_pct": 100, "executed": order["side"],
                     "rule": order["why"], "qty": round(qty, 8), "eur": 0,
                     "coin": round(book["positions"][pair]["coin"], 8), "equity_eur": 0,
                     "reasoning": t("guard_reasoning"), "cycle": "guard"})
    if rows and live:
        try:
            time.sleep(2)  # give market orders a moment to settle
            kraken.sync_live(cfg, state, prices)
            book = state
        except Exception as e:
            print(t("balance_read_failed", err=e))
    for row in rows:
        row["eur"], row["equity_eur"] = round(book["eur"], 2), round(portfolio.equity_of(book, prices), 2)
    return rows
