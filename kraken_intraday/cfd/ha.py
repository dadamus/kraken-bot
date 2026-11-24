"""Home Assistant: entities and notifications (the client is the Kraken bot's). An HA error never stops the run.

Entities: sensor.kraken_cfd_equity, sensor.kraken_cfd_<pair> (the open position), sensor.kraken_cfd_decision,
sensor.kraken_cfd_performance, sensor.kraken_cfd_api_cost, sensor.kraken_cfd_kraken (shadow mode: the
closed paper trades with Kraken's costs).
Every opened and closed position is a notification (home_assistant.notify, switch off with notify_trades) -
the same call could be copied by hand into a real platform, which is the user's decision, not the add-on's.
"""
from datetime import datetime

from krakenbot import homeassistant as kha

from . import account, settings
from .texts import t

ENTITY = "sensor.kraken_cfd"


def fmt(x):
    return f"{x:.6g}" if isinstance(x, float) else str(x)


def notify(ha, title, message):
    domain, service = ha["notify"].split(".", 1)
    kha.ha_post(ha, f"services/{domain}/{service}", {"title": title, "message": message})


def name():
    return "CFD live" if settings.LIVE else "CFD paper"


def event_message(cfg, state, prices, e):
    if e["kind"] == "open":
        return (t("notify_open", label=name(), side=e["side"].upper(), pair=e["pair"]),
                t("notify_open_msg", qty=fmt(e["qty"]), price=fmt(e["price"]), margin=round(e["margin"], 2),
                  stop=fmt(e["stop"]), tp=fmt(e["take_profit"]), reasoning=e.get("reasoning", "")[:250]))
    return (t("notify_close", label=name(), side=e["side"].upper(), pair=e["pair"], reason=e["reason"]),
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
        kha.ha_set(ha, f"{ENTITY}_equity", round(eq, 2), f"{name()} equity", "mdi:wallet", "EUR",
                   start_eur=state["start_eur"], balance_eur=round(state["balance"], 2),
                   return_pct=round((eq / state["start_eur"] - 1) * 100, 2),
                   margin_used_eur=round(account.margin_used(cfg, state, prices), 2),
                   margin_level_pct=round(level, 1) if level else None, halted_until=state.get("halted_until"),
                   vs_hold_pp=bench.get("vs_hold_pp"), costs=state.get("costs"),
                   updated=datetime.now().strftime("%Y-%m-%d %H:%M"))
        for pair in cfg["pairs"]:
            pos = state["positions"].get(pair)
            if pos:
                pnl = account.unrealized(cfg, pair, pos, account.price_of(prices, pair, pos), prices)
                kha.ha_set(ha, f"{ENTITY}_{pair.lower()}", pos["side"], f"{name()} {pair}", "mdi:swap-vertical",
                           qty=pos["qty"], entry=pos["entry"], stop=pos["stop"], take_profit=pos["take_profit"],
                           price=prices.get(pair), unrealized_eur=round(pnl, 2), opened=pos["opened"],
                           swap_eur=round(pos["swap_eur"], 2))
            else:
                kha.ha_set(ha, f"{ENTITY}_{pair.lower()}", "flat", f"{name()} {pair}", "mdi:swap-vertical",
                           price=prices.get(pair))
        if calls:
            kha.ha_set(ha, f"{ENTITY}_decision", calls[0]["date"], f"{name()} decision", "mdi:robot",
                       summary=state.get("last_summary", ""),
                       decisions=[{k: r.get(k) for k in ("pair", "action", "confidence", "p_up_3d", "executed",
                                                         "rule", "reasoning")} for r in calls])
        ev = state.get("evaluation")
        if ev:
            kha.ha_set(ha, f"{ENTITY}_performance", ev["return_pct"], f"{name()} return", "mdi:chart-line", "%",
                       **{k: v for k, v in ev.items() if k != "kraken"})  # HA caps a state's attributes at 16 kB
        kraken = (ev or {}).get("kraken")
        if kraken:
            k = kraken["total"]
            kha.ha_set(ha, f"{ENTITY}_kraken", k["kraken_net_eur"], "CFD paper with Kraken's costs", "mdi:scale-balance",
                       "EUR", **k, quotes=(state.get("kraken") or {}).get("quotes"),
                       wallet=(state.get("kraken") or {}).get("account"))
        usage = state.get("api_usage")
        if usage:
            kha.ha_set(ha, f"{ENTITY}_api_cost", usage["cost_usd"], f"{name()} model cost", "mdi:currency-usd",
                       "USD", **usage)
        if ha.get("notify") and ha.get("notify_trades", True):
            for e in events:
                notify(ha, *event_message(cfg, state, prices, e))
    except Exception as e:
        print(f"  [HA] error: {e}")
