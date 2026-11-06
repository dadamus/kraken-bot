"""Command line and the daily cycle.

  python bot.py              # one cycle in paper mode
  python bot.py --offline    # test on fake data (no internet, no API keys)
  python bot.py --report     # performance summary
  python bot.py --live       # real orders (requires "mode": "live" in config.json + API keys)
  python bot.py --ha         # refresh: stop-loss / take-profit on the current price + Home Assistant; asks
                             # the model only for an event-triggered extra cycle (see triggers.py)
  python bot.py --ha --live  # the same with the real Kraken balance
"""
import argparse
import copy
import sys
import time
from datetime import datetime

from . import (claude, config, evaluation, forecast, guard, homeassistant, indicators, journal, kraken, macro,
               market, offline, portfolio, risk, triggers)
from .i18n import set_language, t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--ha", action="store_true")
    args = ap.parse_args()

    config.load_env()
    cfg = config.load_config()
    set_language(cfg)
    if args.live and cfg.get("mode") != "live":
        sys.exit(t("live_refused"))
    if args.live:
        config.use_live_files()
    mode = config.mode_label(cfg, args.live)

    state = portfolio.load_state(cfg)
    if args.report:
        return evaluation.report(cfg, state)
    if args.ha:
        return refresh(cfg, state, args.live, mode)
    return daily_cycle(cfg, state, args.live, args.offline, mode)


def refresh(cfg, state, live, mode):
    """Between cycles: current prices, stop-loss / take-profit, Home Assistant. Asks the model only when an
    event warrants an extra cycle (a stop fired some hours ago, a large move since the last cycle)."""
    prices = {pair: market.fetch_price(pair) for pair in cfg["pairs"]}
    info = kraken.sync_live(cfg, state, prices) if live else None
    rows = guard.guard(cfg, state, prices, live, info)
    if rows:
        journal.log_rows(rows)
    charts = state.setdefault("charts", {})
    missing = [pair for pair in cfg["pairs"] if pair not in charts]
    log = journal.read_log() if missing else []
    built = False
    for pair in missing:  # normally built by the daily cycle; until then from the latest call in the log
        chart = market.optional(f"chart {pair}", forecast.rebuild, pair, log)
        if chart:  # a failed source is simply retried on the next refresh
            charts[pair], built = chart, True
    if live or rows or built:
        portfolio.save_state(state)
    homeassistant.ha_publish(cfg, state, prices, mode, rows)

    reason = triggers.extra_cycle_reason(cfg, state, prices, journal.read_log())
    if reason:
        triggers.count_extra(state)
        portfolio.save_state(state)
        print(t("extra_cycle", why=triggers.describe(reason)))
        daily_cycle(cfg, state, live, False, mode, trigger=reason)


def daily_cycle(cfg, state, live, offline_mode, mode, trigger=None):
    pairs = cfg["pairs"]
    print(t("cycle_header", now=f"{datetime.now():%Y-%m-%d %H:%M}", pairs=", ".join(pairs), mode=mode.upper()))

    markets, prices, candles, closed_all = load_markets(pairs, state, offline_mode)
    news = [t("offline_news")] if offline_mode else market.fetch_news(cfg["news_feeds"])
    print(t("news_count", n=len(news)))
    pair_info = kraken.sync_live(cfg, state, prices) if live else None
    context = {"this_cycle": triggers.cycle_info(trigger),
               **load_context(pairs, state, prices, candles, closed_all, offline_mode)}

    # positions outside the pair list (e.g. after removing a pair from config.json) - no price, no value
    for pair, p in state["positions"].items():
        if pair not in pairs and p["coin"] > 0:
            print(t("untracked_position", pair=pair, coin=p["coin"]))

    if offline_mode:
        signals, summary = {pair: offline.fake_signal() for pair in pairs}, "OFFLINE TEST"
    else:
        signals, summary = claude.ask_claude(cfg, markets, news, portfolio_view(state, prices, pairs), context)
    print(t("model_summary", summary=summary))
    made = datetime.now().strftime("%Y-%m-%d %H:%M")
    state["charts"] = {pair: forecast.chart(closed_all[pair], prices[pair], signals[pair], markets[pair], made)
                       for pair in pairs}

    # In live the budget during the cycle is tracked on a copy (an estimate after orders) so several buys
    # at once cannot exceed the limits; at the end the real Kraken balance is read anyway.
    book = copy.deepcopy(state) if live else state
    rows = execute_signals(cfg, state, book, markets, prices, signals, live, pair_info, mode)

    if live:
        try:
            time.sleep(2)  # give market orders a moment to settle
            for pair in pairs:  # stops set in this cycle only live in the working copy
                state["positions"][pair]["stop_price"] = book["positions"][pair].get("stop_price")
            kraken.sync_live(cfg, state, prices)
            book = state
        except Exception as e:
            print(t("balance_read_failed", err=e))
    equity = portfolio.equity_of(book, prices)
    for row in rows:
        row["eur"], row["equity_eur"] = round(book["eur"], 2), round(equity, 2)
    state["last_summary"] = summary
    triggers.remember_cycle(state, markets, prices)
    journal.log_rows(rows)
    ev = market.optional("evaluation", evaluation.evaluation, cfg, book, journal.read_log(), candles, prices)
    if ev:
        state["evaluation"] = book["evaluation"] = ev
        for line in evaluation.evaluation_lines(ev):
            print(f"  {line}")
    portfolio.save_state(state)
    print(t("equity_line", equity=equity, start=state["start_eur"]))
    homeassistant.ha_publish(cfg, book, prices, mode, rows)


def load_markets(pairs, state, offline_mode):
    """Candles, current prices and indicators per pair. Returns (markets, prices, candles, closed_all):
    candles only holds real Kraken data (used for settling the model's calls), closed_all also fake data."""
    markets, prices, candles, closed_all = {}, {}, {}, {}
    for i, pair in enumerate(pairs):
        if offline_mode:
            closed, price = offline.fake_market(60000.0 / (i * 10 + 1))
        else:  # 720 days (Kraken's maximum): SMA200, the yearly picture, settling the model's calls
            closed, price = market.fetch_ohlc(pair, 720)
            candles[pair] = closed
        closed_all[pair] = closed
        prices[pair] = price
        markets[pair] = m = indicators.indicators(closed, price)
        if not offline_mode:  # the intraday path; a failure only leaves it out
            hours = market.optional(f"hourly {pair}", market.fetch_hourly, pair)
            if hours:
                m["last_24_hours"] = indicators.hourly(hours, price)
        state["first_prices"].setdefault(pair, price)
        print(t("price_line", pair=pair, price=price, d1=m["change_1d_pct"], d7=m["change_7d_pct"],
                sma200=m["pct_vs_sma200"], atr=m["atr14_pct"]))
    for pair, rs in indicators.relative_strength(closed_all, prices).items():
        markets[pair]["vs_btc"] = rs
        print(t("vs_btc_line", pair=pair, d7=rs["change_7d_pct"], d30=rs["change_30d_pct"]))
    return markets, prices, candles, closed_all


def load_context(pairs, state, prices, candles, closed_all, offline_mode):
    """Extra context for the model: its own track record and the optional market-wide sources."""
    context = {"own_recent_decisions": claude.recent_decisions(pairs, prices),
               "own_track_record": market.optional("track record", evaluation.evaluate_signals, journal.read_log(),
                                                   candles, None if offline_mode else prices)}
    if not offline_mode:
        context["derivatives"] = market.optional("futures", market.fetch_derivatives, pairs, state)
        context["fear_greed"] = market.optional("fear&greed", market.fetch_fear_greed)
        context["market_overview"] = market.optional("coingecko", market.fetch_market_overview, state)
        context["implied_vol"] = market.optional("deribit", market.fetch_implied_vol, pairs, closed_all)
        context["us_markets"] = market.optional("FRED", macro.fetch_us_markets)
        context["macro_calendar"] = market.optional("calendar", macro.fetch_macro_calendar, state)
        context["coinbase_premium"] = market.optional("coinbase", macro.fetch_coinbase_premium)
    if context.get("market_overview"):
        mo = context["market_overview"]
        print(t("market_line", btc=mo["btc_dominance_pct"], stable=mo["stablecoin_share_pct"],
                cap=mo["total_market_cap_change_24h_pct"]))
    for pair, iv in (context.get("implied_vol") or {}).items():
        print(t("dvol_line", pair=pair, dvol=iv["dvol_now"], move=iv["implied_daily_move_pct"],
                realized=iv["realized_vol_30d_annual_pct"]))
    for pair, d in (context.get("derivatives") or {}).items():
        print(t("funding_line", pair=pair, funding=d["funding_8h_pct"], oi=d["open_interest_usd_m"],
                oi_chg=d["open_interest_change_pct"]))
    us = context.get("us_markets") or {}
    if us:
        print(t("us_markets_line", items=", ".join(
            f"{k} {v['last']:g} ({v.get('change_1d_pct', v.get('change_1d_points'))})" for k, v in us.items())))
    if context.get("macro_calendar") is not None:
        print(t("calendar_line", n=len(context["macro_calendar"]),
                items="; ".join(f"{e['time_utc']} {e['event']}" for e in context["macro_calendar"][:4])))
    if context.get("coinbase_premium"):
        cp = context["coinbase_premium"]
        print(t("premium_line", now=cp["premium_now_pct"], avg=cp["premium_avg_24h_pct"]))
    if context.get("fear_greed"):
        print(f"  Fear & Greed: {context['fear_greed']['today']} ({context['fear_greed']['label']})")
    return context


def portfolio_view(state, prices, pairs):
    """The portfolio as the model sees it."""
    positions = state["positions"]
    return {"eur_free": round(state["eur"], 2), "equity_eur": round(portfolio.equity_of(state, prices), 2),
            "positions": {pair: {"coin": positions[pair]["coin"],
                                 "entry_price": positions[pair]["entry_price"],
                                 "stop_price": positions[pair].get("stop_price"),
                                 "value_eur": round(positions[pair]["coin"] * prices[pair], 2)}
                          for pair in pairs}}


def execute_signals(cfg, state, book, markets, prices, signals, live, pair_info, mode):
    """Turns the model's signals into orders under the risk rules and executes them on `book`.
    Returns the log rows (eur / equity_eur are filled in by the caller after the final balance)."""
    pairs = cfg["pairs"]
    halt = risk.check_halt(cfg, state, prices)
    book["halted_until"] = state.get("halted_until")

    orders = {}
    for pair in pairs:
        exit_order = risk.protective_exit(cfg, book["positions"][pair], prices[pair])
        if exit_order:
            orders[pair] = exit_order
        elif halt:
            orders[pair] = {"side": "none", "why": halt}
    # sells first (they free EUR and exposure room), then buys
    ordered = sorted(pairs, key=lambda p: 0 if signals[p]["action"] == "sell" else 1)

    rows, now_s = [], datetime.now().strftime("%Y-%m-%d %H:%M")
    for pair in ordered:
        sig, price, atr = signals[pair], prices[pair], markets[pair].get("atr14")
        order = orders.get(pair) or risk.decide(cfg, book, pair, prices, sig, atr)
        if order["side"] == "none" and sig["action"] != "sell":
            raised = risk.tighten_stop(cfg, book["positions"][pair], sig, price, atr)
            if raised:
                order["why"] += t("why_stop_set", stop=raised)
        p_up = "-" if sig["p_up_3d"] is None else f"{sig['p_up_3d']:.2f}"
        print(t("decision_line", pair=pair, action=sig["action"], conf=sig["confidence"], size=sig["size_pct"],
                p_up=p_up, side=order["side"], why=order["why"]))
        print(f"             {sig['reasoning']}")
        qty = 0.0
        if order["side"] in ("buy", "sell"):
            if live and not kraken.live_execute(cfg, pair_info[pair], order, price):
                order = {"side": "error", "why": t("why_order_failed", why=order["why"])}
            else:
                if live and cfg.get("live_validate_only", True):
                    order["why"] += t("why_validate_buy" if order["side"] == "buy" else "why_validate_sell")
                fill, qty = portfolio.paper_execute(cfg, book, pair, order, price)
                print(t("fill_line", mode=mode, side=order["side"], qty=qty, fill=fill))
        rows.append({"date": now_s, "pair": pair, "price": round(price, 6),
                     "model_action": sig["action"], "confidence": sig["confidence"],
                     "p_up_3d": sig["p_up_3d"], "size_pct": sig["size_pct"], "executed": order["side"],
                     "rule": order["why"], "qty": round(qty, 8), "eur": 0,
                     "coin": round(book["positions"][pair]["coin"], 8), "equity_eur": 0,
                     "reasoning": sig["reasoning"]})
    return rows
