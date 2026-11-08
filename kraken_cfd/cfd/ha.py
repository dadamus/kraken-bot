"""Home Assistant: entities and notifications (the client is the Kraken bot's). An HA error never stops the run.

Entities: sensor.kraken_cfd_equity, sensor.kraken_cfd_<pair> (the open position), sensor.kraken_cfd_decision,
sensor.kraken_cfd_performance, sensor.kraken_cfd_api_cost.
Every opened and closed position is a notification (home_assistant.notify, switch off with notify_trades) -
the same call could be copied by hand into a real platform, which is the user's decision, not the add-on's.
"""
from datetime import datetime

from krakenbot import homeassistant as kha

from . import account
from .texts import t

ENTITY = "sensor.kraken_cfd"


def fmt(x):
    return f"{x:.6g}" if isinstance(x, float) else str(x)


def notify(ha, title, message):
    domain, service = ha["notify"].split(".", 1)
    kha.ha_post(ha, f"services/{domain}/{service}", {"title": title, "message": message})


def event_message(cfg, state, prices, e):
    if e["kind"] == "open":
        return (t("notify_open", side=e["side"].upper(), pair=e["pair"]),
                t("notify_open_msg", qty=fmt(e["qty"]), price=fmt(e["price"]), margin=round(e["margin"], 2),
                  stop=fmt(e["stop"]), tp=fmt(e["take_profit"]), reasoning=e.get("reasoning", "")[:250]))
    return (t("notify_close", side=e["side"].upper(), pair=e["pair"], reason=e["reason"]),
            t("notify_close_msg", net=e["net_eur"], move=e["move_pct"], swap=e["swap_eur"], spread=e["spread_eur"],
              equity=round(account.equity(cfg, state, prices), 2)))


def publish(cfg, state, prices, events=(), calls=()):
    ha = kha.ha_conf(cfg)
    if not ha:
        return
    try:
        eq = account.equity(cfg, state, prices)
        level = account.margin_level(cfg, state, prices)
        bench = (state.get("evaluation") or {}).get("benchmark") or {}
        kha.ha_set(ha, f"{ENTITY}_equity", round(eq, 2), "CFD paper equity", "mdi:wallet", "EUR",
                   start_eur=state["start_eur"], balance_eur=round(state["balance"], 2),
                   return_pct=round((eq / state["start_eur"] - 1) * 100, 2),
                   margin_used_eur=round(account.margin_used(cfg, state, prices), 2),
                   margin_level_pct=round(level, 1) if level else None, halted_until=state.get("halted_until"),
                   vs_hold_pp=bench.get("vs_hold_pp"), costs=state.get("costs"),
                   updated=datetime.now().strftime("%Y-%m-%d %H:%M"))
        for pair in cfg["pairs"]:
            pos = state["positions"].get(pair)
            if pos:
                pnl = account.unrealized(cfg, pair, pos, account.price_of(prices, pair, pos))
                kha.ha_set(ha, f"{ENTITY}_{pair.lower()}", pos["side"], f"CFD paper {pair}", "mdi:swap-vertical",
                           qty=pos["qty"], entry=pos["entry"], stop=pos["stop"], take_profit=pos["take_profit"],
                           price=prices.get(pair), unrealized_eur=round(pnl, 2), opened=pos["opened"],
                           swap_eur=round(pos["swap_eur"], 2))
            else:
                kha.ha_set(ha, f"{ENTITY}_{pair.lower()}", "flat", f"CFD paper {pair}", "mdi:swap-vertical",
                           price=prices.get(pair))
        if calls:
            kha.ha_set(ha, f"{ENTITY}_decision", calls[0]["date"], "CFD paper decision", "mdi:robot",
                       summary=state.get("last_summary", ""),
                       decisions=[{k: r.get(k) for k in ("pair", "action", "confidence", "p_up_3d", "executed",
                                                         "rule", "reasoning")} for r in calls])
        ev = state.get("evaluation")
        if ev:
            kha.ha_set(ha, f"{ENTITY}_performance", ev["return_pct"], "CFD paper return", "mdi:chart-line", "%",
                       **ev)
        usage = state.get("api_usage")
        if usage:
            kha.ha_set(ha, f"{ENTITY}_api_cost", usage["cost_usd"], "CFD paper model cost", "mdi:currency-usd",
                       "USD", **usage)
        if ha.get("notify") and ha.get("notify_trades", True):
            for e in events:
                notify(ha, *event_message(cfg, state, prices, e))
    except Exception as e:
        print(f"  [HA] error: {e}")
