"""Home Assistant: entities and notifications (the client is the Kraken bot's). An HA error never stops the run.

Entities: sensor.kraken_intraday_equity (the model book), sensor.kraken_intraday_rules_equity,
sensor.kraken_intraday_regime, sensor.kraken_intraday_performance, sensor.kraken_intraday_api_cost.
Notifications (home_assistant.notify): every trade of the model book when notify_trades is on (off by default:
an intraday book trades several times a day), and yesterday's result after the daily cycle (daily_summary).
"""
from datetime import datetime, timedelta

from cfd import account
from krakenbot import homeassistant as kha

from .texts import t

ENTITY = "sensor.kraken_intraday"


def fmt(x):
    return f"{x:.6g}" if isinstance(x, float) else str(x)


def notify(ha, title, message):
    domain, service = ha["notify"].split(".", 1)
    kha.ha_post(ha, f"services/{domain}/{service}", {"title": title, "message": message})


def event_message(cfg, state, prices, e):
    if e["kind"] == "open":
        return (t("notify_open", side=e["side"].upper(), pair=e["pair"], strategy=e["strategy"]),
                t("notify_open_msg", qty=fmt(e["qty"]), price=fmt(e["price"]), stop=fmt(e["stop"]),
                  tp=fmt(e["take_profit"]), why=e.get("why", "")))
    eq = account.equity(cfg, state["books"][e.get("book", "model")], prices)
    return (t("notify_close", side=e["side"].upper(), pair=e["pair"], reason=e["reason"]),
            t("notify_close_msg", net=e["net_eur"], move=e["move_pct"], equity=round(eq, 2)))


def yesterday(ev, name, now):
    day = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    last = (((ev.get("books") or {}).get(name) or {}).get("days") or {}).get("last") or []
    return next((r["pnl_eur"] for r in last if r["date"] == day), 0.0)


def compact(ev):
    """The evaluation without the long lists (Home Assistant limits the size of attributes)."""
    books = {name: {k: ({**v, "last": None} if k == "days" else v) for k, v in b.items()}
             for name, b in (ev.get("books") or {}).items()}
    return {"date": ev.get("date"), "books": books, "regimes": ev.get("regimes")}


def publish(cfg, state, prices, events=(), daily=False, now=None):
    ha = kha.ha_conf(cfg)
    if not ha:
        return
    now = now or datetime.now()
    try:
        ev = state.get("evaluation") or {}
        for name, entity, label in (("model", "equity", "Intraday paper equity (model)"),
                                    ("rules", "rules_equity", "Intraday paper equity (rules)")):
            book = state["books"][name]
            eq = account.equity(cfg, book, prices)
            b = (ev.get("books") or {}).get(name) or {}
            days = b.get("days") or {}
            kha.ha_set(ha, f"{ENTITY}_{entity}", round(eq, 2), label, "mdi:wallet", "EUR",
                       start_eur=book["start_eur"], return_pct=round((eq / book["start_eur"] - 1) * 100, 2),
                       today_eur=days.get("today_eur"), green_days=days.get("green"), days=days.get("n"),
                       vs_hold_pp=(b.get("benchmark") or {}).get("vs_hold_pp"), costs=book.get("costs"),
                       halted_until=book.get("halted_until"),
                       positions={p: {"side": pos["side"], "strategy": pos.get("strategy"), "stop": pos["stop"],
                                      "take_profit": pos["take_profit"]} for p, pos in book["positions"].items()},
                       updated=now.strftime("%Y-%m-%d %H:%M"))
        if state.get("last_cycle"):
            kha.ha_set(ha, f"{ENTITY}_regime", state["last_cycle"], "Intraday paper regimes", "mdi:compass",
                       summary=state.get("last_summary", ""), regimes=state.get("regimes"),
                       last_step=state.get("last_step"))
        if ev:
            model_book = (ev.get("books") or {}).get("model") or {}
            kha.ha_set(ha, f"{ENTITY}_performance", model_book.get("return_pct"), "Intraday paper return",
                       "mdi:chart-line", "%", **compact(ev))
        usage = state.get("api_usage")
        if usage:
            kha.ha_set(ha, f"{ENTITY}_api_cost", usage["cost_usd"], "Intraday paper model cost",
                       "mdi:currency-usd", "USD", **usage)
        if ha.get("notify") and ha.get("notify_trades", False):
            for e in events:
                if e.get("book", "model") == "model":
                    notify(ha, *event_message(cfg, state, prices, e))
        if ha.get("notify") and daily and ha.get("daily_summary", True) and ev:
            notify(ha, t("notify_daily"), t(
                "notify_daily_msg", model=f"{yesterday(ev, 'model', now):+.2f}", rules=f"{yesterday(ev, 'rules', now):+.2f}",
                eq_model=round(account.equity(cfg, state["books"]["model"], prices), 2),
                eq_rules=round(account.equity(cfg, state["books"]["rules"], prices), 2),
                summary=state.get("last_summary", "")[:200]))
    except Exception as e:
        print(f"  [HA] error: {e}")
