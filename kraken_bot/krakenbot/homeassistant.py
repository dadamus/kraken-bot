"""Home Assistant: entities, logbook entries and notifications. An HA error never stops the bot."""
import os
from datetime import datetime

import requests

from . import journal, portfolio
from .i18n import t


ENTITY = "sensor.kraken_bot"  # Home Assistant entity prefix


def ha_conf(cfg):
    ha = cfg.get("home_assistant") or {}
    if not ha.get("enabled"):
        return None
    url = os.environ.get("HA_URL") or ha.get("url")  # the add-on sets HA_URL and HA_TOKEN itself
    token = os.environ.get("HA_TOKEN")
    if not url or not token:
        print(t("ha_missing"))
        return None
    return {**ha, "url": url.rstrip("/"), "token": token}


def ha_post(ha, path, body):
    r = requests.post(f"{ha['url']}/api/{path}", json=body, timeout=10,
                      headers={"Authorization": f"Bearer {ha['token']}"})
    r.raise_for_status()


def ha_set(ha, entity, value, name, icon, unit=None, **attrs):
    attrs = {"friendly_name": name, "icon": icon, **attrs}
    if unit:
        attrs.update(unit_of_measurement=unit, state_class="measurement")
    ha_post(ha, f"states/{entity}", {"state": value, "attributes": attrs})


def ha_publish(cfg, state, prices, mode, new_rows=None):
    """Sends the portfolio and the decision log to Home Assistant. An HA error never stops the bot."""
    ha = ha_conf(cfg)
    if not ha:
        return
    try:
        p = ENTITY
        equity = portfolio.equity_of(state, prices)
        pnl = (equity / state["start_eur"] - 1) * 100
        common = {"mode": mode, "updated": datetime.now().strftime("%Y-%m-%d %H:%M")}
        ha_set(ha, f"{p}_equity", round(equity, 2), t("ha_equity"), "mdi:wallet", "EUR",
               start_eur=state["start_eur"], eur_free=round(state["eur"], 2),
               halted_until=state.get("halted_until"), **common)
        ha_set(ha, f"{p}_return", round(pnl, 2), t("ha_return"), "mdi:chart-line", "%", **common)
        ha_set(ha, f"{p}_free_eur", round(state["eur"], 2), t("ha_free_eur"), "mdi:cash", "EUR")
        for pair, price in prices.items():
            pos = state["positions"].get(pair, portfolio.empty_position())
            entry, first = pos["entry_price"], state["first_prices"].get(pair)
            ha_set(ha, f"{p}_{pair.lower()}", round(pos["coin"] * price, 2), f"Kraken bot {pair}",
                   "mdi:bitcoin", "EUR", coin=pos["coin"], price=price, entry_price=round(entry, 2) if entry else None,
                   entry_source=pos.get("entry_source"), stop_price=pos.get("stop_price"),
                   position_pnl_pct=round((price / entry - 1) * 100, 2) if entry else None,
                   buy_and_hold_pct=round((price / first - 1) * 100, 2) if first else None)
            chart = (state.get("charts") or {}).get(pair)
            if chart:  # state: expected 3-day change in %; attributes: the data for the price chart
                expected = chart.get("expected_3d_pct")
                ha_set(ha, f"{p}_{pair.lower()}_forecast", expected if expected is not None else "unknown",
                       t("ha_forecast", pair=pair), "mdi:crystal-ball", "%", **chart)

        log = journal.read_log()
        last_date = log[-1]["date"] if log else None
        keep = ("date", "pair", "price", "model_action", "confidence", "p_up_3d", "executed", "rule")
        ha_set(ha, f"{p}_decision", last_date or t("ha_no_decision"), t("ha_decision"), "mdi:robot",
               summary=state.get("last_summary", ""),
               decisions=[{k: r.get(k) for k in keep + ("reasoning",)} for r in log if r["date"] == last_date],
               history=[{k: r.get(k) for k in keep} for r in log[-30:]])

        ev = state.get("evaluation")
        if ev:  # computed in the daily cycle; entity state = direction hit rate of p_up_3d
            hit = ev["p_up_3d"]["hit_rate_pct"]
            ha_set(ha, f"{p}_performance", hit if hit is not None else "unknown", t("ha_performance"),
                   "mdi:bullseye-arrow", "%", **ev)

        for r in new_rows or []:
            ha_post(ha, "services/logbook/log",
                    {"name": f"Kraken bot {r['pair']}", "entity_id": f"{p}_decision",
                     "message": f"model {r['model_action']} ({r['confidence']:.2f}) -> {r['executed']}"
                                f" ({r['rule']}). {r['reasoning'][:250]}"})
            if r["executed"] in ("buy", "sell") and ha.get("notify"):
                domain, service = ha["notify"].split(".", 1)
                ha_post(ha, f"services/{domain}/{service}",
                        {"title": f"Kraken bot [{mode}]: {r['executed']} {r['pair']}",
                         "message": t("ha_notify_message", qty=r["qty"], price=r["price"], rule=r["rule"],
                                      equity=equity)})
        print(t("ha_updated", url=ha["url"]))
    except Exception as e:
        print(t("ha_error", err=e))
