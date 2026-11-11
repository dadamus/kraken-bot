"""Command line, the daily cycle and the refresh.

  python cfd_bot.py              # the daily cycle: data, the model's calls, the simulated orders
  python cfd_bot.py --offline    # the same on fake data with random calls (no internet, no API key)
  python cfd_bot.py --refresh    # between cycles: stops / take-profits, swap, stop-out, Home Assistant
  python cfd_bot.py --report     # the latest evaluation
"""
import argparse
from datetime import datetime, timedelta

from krakenbot import costs, i18n as kb_i18n, indicators, macro, market, offline

from . import account, guard, ha, journal, model, report, risk, settings, texts

FMT = "%Y-%m-%d %H:%M"


def main(argv=None):
    ap = argparse.ArgumentParser(description="CFD paper simulator (no real orders)")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)
    cfg = settings.load_config()
    texts.set_language(cfg)
    kb_i18n.set_language(cfg)  # messages of the shared market-data code
    state = account.load_state(cfg)
    if args.report:
        print(report.report_text(state))
    elif args.refresh:
        refresh(cfg, state)
    else:
        daily_cycle(cfg, state, offline_mode=args.offline)


def fetch_prices(cfg, state):
    prices = {pair: market.fetch_price(pair) for pair in cfg["pairs"]}
    for pair, pos in state["positions"].items():  # a pair removed from config.json is still valued
        if pair not in prices:
            prices[pair] = market.optional(f"price {pair}", market.fetch_price, pair) or pos["last_price"]
    if "EURUSD" not in prices and any(account.instrument(cfg, p)["quote"] == "USD" for p in prices):
        prices["EURUSD"] = market.fetch_price("EURUSD")  # to value USD-quoted pairs in EUR
    return prices


def protect(cfg, state, prices, now, fetch=guard.fetch_candles):
    """Stops / take-profits since the last check, the overnight swap, the margin stop-out. Returns the closed
    trades."""
    trades = guard.check(cfg, state, prices, now, fetch)
    account.charge_swaps(cfg, state, prices, now)
    trades += account.stop_out(cfg, state, prices, now)
    for pair, pos in state["positions"].items():
        pos["last_price"] = prices.get(pair, pos["last_price"])
    state["last_check"] = int(now.timestamp())
    state["last_prices"] = {pair: prices[pair] for pair in prices}
    state["last_refresh"] = now.strftime(FMT)
    risk.check_halt(cfg, state, prices, now)  # keeps the peak equity current
    for tr in trades:
        print(f"  {tr['pair']} {tr['side']} closed ({tr['reason']}) at {tr['exit']:g}: net {tr['net_eur']:+} EUR")
    return trades


def refresh(cfg, state, now=None):
    now = now or datetime.now()
    prices = fetch_prices(cfg, state)
    trades = protect(cfg, state, prices, now)
    journal.log_trades(trades)
    account.save_state(state)
    ha.publish(cfg, state, prices, [{"kind": "close", **tr} for tr in trades])


def load_markets(pairs, offline_mode, crypto=None):
    """Candles, prices and indicators per pair. `crypto`: the crypto pairs (vs_btc only makes sense for them)."""
    markets, prices, closed_all = {}, {}, {}
    for i, pair in enumerate(pairs):
        if offline_mode:
            closed, price = offline.fake_market(60000.0 / (i * 10 + 1))
        else:  # 720 days (Kraken's maximum): SMA200, the yearly picture, settling the calls
            closed, price = market.fetch_ohlc(pair, 720)
        closed_all[pair], prices[pair] = closed, price
        markets[pair] = m = indicators.indicators(closed, price)
        if not offline_mode:
            hours = market.optional(f"hourly {pair}", market.fetch_hourly, pair)
            if hours:
                m["last_24_hours"] = indicators.hourly(hours, price)
        print(f"  {pair}: {price:g}, 1d {m['change_1d_pct']}%, 7d {m['change_7d_pct']}%,"
              f" vs SMA200 {m['pct_vs_sma200']}%, ATR {m['atr14_pct']}%")
    crypto = pairs if crypto is None else crypto
    for pair, rs in indicators.relative_strength({p: closed_all[p] for p in crypto}, prices).items():
        markets[pair]["vs_btc"] = rs
    return markets, prices, closed_all


def load_context(pairs, state, closed_all):
    """The same public market-wide sources the Kraken bot uses; a failing one is just left out. `pairs`: the
    crypto pairs (perpetual funding and options volatility exist only for them)."""
    return {"derivatives": market.optional("futures", market.fetch_derivatives, pairs, state),
            "fear_greed": market.optional("fear&greed", market.fetch_fear_greed),
            "market_overview": market.optional("coingecko", market.fetch_market_overview, state),
            "implied_vol": market.optional("deribit", market.fetch_implied_vol, pairs, closed_all),
            "us_markets": market.optional("FRED", macro.fetch_us_markets),
            "macro_calendar": market.optional("calendar", macro.fetch_macro_calendar, state),
            "coinbase_premium": market.optional("coinbase", macro.fetch_coinbase_premium)}


def account_view(cfg, state, prices, now):
    level = account.margin_level(cfg, state, prices)
    positions = {}
    for pair, pos in state["positions"].items():
        price = account.price_of(prices, pair, pos)
        sign = 1 if pos["side"] == "long" else -1
        positions[pair] = {"side": pos["side"], "qty": pos["qty"], "entry": round(pos["entry"], 6),
                           "stop": pos["stop"], "take_profit": pos["take_profit"],
                           "unrealized_eur": round(account.unrealized(cfg, pair, pos, price, prices), 2),
                           "move_in_direction_pct": round((price / pos["entry_mid"] - 1) * 100 * sign, 2),
                           "days_open": round((now.timestamp() - pos["opened_ts"]) / 86400, 1),
                           "swap_paid_eur": round(pos["swap_eur"], 2),
                           "market_open_now": account.market_open(cfg, pair, now),
                           "stop_at_breakeven": bool(pos.get("breakeven"))}
    return {"balance_eur": round(state["balance"], 2), "equity_eur": round(account.equity(cfg, state, prices), 2),
            "margin_used_eur": round(account.margin_used(cfg, state, prices), 2),
            "margin_level_pct": round(level, 1) if level else None,
            "halted_until": state.get("halted_until"), "positions": positions}


def trading_terms(cfg, now=None, state=None):
    r = cfg["risk"]
    now = now or datetime.now()
    event = risk.event_ahead(cfg, state or {}, now)
    instruments = {}
    for pair in cfg["pairs"]:
        inst = account.instrument(cfg, pair)
        instruments[pair] = {
            "name": inst["name"], "asset_class": inst["asset_class"],
            "price_in": inst["quote"] if inst["quote"] != "EUR" else "EUR",
            "leverage": inst["leverage"], "margin_pct_of_notional": round(100 / inst["leverage"], 2),
            "round_trip_spread_pct": inst["spread_pct"], "overnight_swap_pct_per_day": inst["swap_pct_per_day"],
            "trading_hours": (f"closed {inst['weekend_close']} - {inst['weekend_open']} (local time)"
                              if inst["weekend_close"] else "24/7"),
            "market_open_now": account.market_open(cfg, pair, now)}
    # the model is not told the account is simulated: it should trade it like a real one
    return {"account_currency": "EUR", "style": model.style(cfg), "allow_short": cfg.get("allow_short", True),
            "instruments": instruments, "stop_out_margin_level_pct": cfg["stop_out_margin_level_pct"],
            **{k: r[k] for k in ("min_confidence", "risk_per_trade_pct", "min_stop_atr", "default_stop_atr",
                                 "max_stop_pct", "default_reward_risk", "event_stop_atr", "event_window_hours")},
            "breakeven_r": r.get("breakeven_r", 0),
            "min_stop_atr_now": risk.min_stop_atr(cfg, event),
            "release_ahead": {"event": event["event"], "time_utc": event["time_utc"], "in_hours": event["in_hours"]}
            if event else None}


def recent(calls, trades, prices, now, days=7):
    """The model's calls of the last `days` days with the move since, and the trades closed in that time."""
    since = (now - timedelta(days=days)).strftime(FMT)

    def change(r):
        try:
            return round((prices[r["pair"]] / float(r["price"]) - 1) * 100, 2)
        except (KeyError, ValueError, ZeroDivisionError):
            return None

    out_calls = [{"date": r["date"], "pair": r["pair"], "action": r["action"], "confidence": r["confidence"],
                  "p_up_3d": r["p_up_3d"], "executed": r["executed"], "price_then": r["price"],
                  "price_change_since_pct": change(r)} for r in calls if r["date"] >= since]
    out_trades = [{k: r[k] for k in ("pair", "side", "opened", "closed", "reason", "move_pct", "net_eur", "days")}
                  for r in trades if r["closed"] >= since]
    return out_calls, out_trades


def execute(cfg, state, prices, markets, signals, halt, now):
    """Turns the calls into simulated orders: every close first (they free margin), then the opens.
    Returns (calls.csv rows, closed trades, events for the notifications)."""
    pairs = cfg["pairs"]
    event = risk.event_ahead(cfg, state, now)
    plans = {pair: risk.decide(cfg, state, pair, prices[pair], signals[pair], markets[pair].get("atr14"), halt, event)
             if account.market_open(cfg, pair, now)
             else [{"do": "none", "why": f"model: {signals[pair]['action']}, market closed (weekend)"}]
             for pair in pairs}
    done = {pair: [] for pair in pairs}
    trades, events = [], []
    for pair in pairs:
        for step in plans[pair]:
            if step["do"] == "close" and pair in state["positions"]:
                tr = account.close_position(cfg, state, pair, prices[pair], f"model: {signals[pair]['action']}", now,
                                            prices)
                trades.append(tr)
                events.append({"kind": "close", **tr})
                done[pair].append(f"close_{tr['side']}")
    for pair in pairs:
        for step in plans[pair]:
            if step["do"] != "open":
                continue
            qty, why = risk.size(cfg, state, prices, pair, prices[pair], step["dist"])
            if not qty:
                step["why"] += f" -> not opened: {why}"
                continue
            pos = account.open_position(cfg, state, pair, step["side"], qty, prices[pair], step["stop"],
                                        step["take_profit"], now, prices)
            done[pair].append(f"open_{step['side']}")
            events.append({"kind": "open", "pair": pair, "side": step["side"], "qty": qty, "price": pos["entry"],
                           "margin": account.notional_eur(cfg, pair, qty, prices[pair], prices)
                           / account.leverage(cfg, pair), "stop": pos["stop"],
                           "take_profit": pos["take_profit"], "reasoning": signals[pair]["reasoning"]})
    eq = round(account.equity(cfg, state, prices), 2)
    rows, now_s = [], now.strftime(FMT)
    for pair in pairs:
        sig = signals[pair]
        rule = "; ".join(s["why"] for s in plans[pair])
        executed = "+".join(done[pair]) or "none"
        p_up = "-" if sig["p_up_3d"] is None else f"{sig['p_up_3d']:.2f}"
        print(f"  {pair}: model {sig['action']} ({sig['confidence']:.2f}, p_up {p_up}) -> {executed}: {rule}")
        print(f"             {sig['reasoning']}")
        rows.append({"date": now_s, "pair": pair, "price": round(prices[pair], 6), "action": sig["action"],
                     "confidence": sig["confidence"], "p_up_3d": sig["p_up_3d"], "stop_price": sig["stop_price"],
                     "take_profit": sig["take_profit"], "executed": executed, "rule": rule, "equity_eur": eq,
                     "reasoning": sig["reasoning"]})
    return rows, trades, events


def daily_cycle(cfg, state, offline_mode=False, now=None, ask=None):
    now = now or datetime.now()
    pairs = cfg["pairs"]
    print(f"=== CFD paper {now:%Y-%m-%d %H:%M}: {', '.join(pairs)}{' (OFFLINE)' if offline_mode else ''} ===")
    crypto = [pair for pair in pairs if account.is_crypto(cfg, pair)]
    markets, prices, closed_all = load_markets(pairs, offline_mode, crypto)
    if not offline_mode and "EURUSD" not in prices and any(account.instrument(cfg, p)["quote"] == "USD" for p in pairs):
        prices["EURUSD"] = market.fetch_price("EURUSD")  # to value USD-quoted pairs in EUR
    for pair in pairs:
        state["first_prices"].setdefault(pair, prices[pair])
    trades = protect(cfg, state, prices, now, fetch=None if offline_mode else guard.fetch_candles)
    news = [] if offline_mode else market.fetch_news(cfg["news_feeds"])
    context = {} if offline_mode else load_context(crypto, state, closed_all)
    calls_log, trades_log = journal.read_calls(), journal.read_trades() + trades
    recent_calls, recent_trades = recent(calls_log, trades_log, prices, now)
    payload = {"markets": markets, "account": account_view(cfg, state, prices, now),
               "trading_terms": trading_terms(cfg, now, state), "news_last_24h": news,
               "own_recent_calls": recent_calls, "own_recent_trades": recent_trades,
               "own_track_record": (state.get("evaluation") or {}).get("calls"), **context}

    if offline_mode:
        signals, summary = {pair: model.fake_signal() for pair in pairs}, "OFFLINE TEST"
    else:
        model.last_usage = None
        try:
            signals, summary = (ask or model.ask)(cfg, payload)
        finally:  # a call is paid for even when the cycle fails afterwards
            if model.last_usage:
                cost = costs.record(cfg, state, model.last_usage, "daily")
                print(f"  model cost {cost:.3f} USD (this month {state['api_usage']['cost_usd']:.2f} USD)")
                account.save_state(state)
    print(f"  model: {summary}")

    halt = risk.check_halt(cfg, state, prices, now)
    rows, closed, events = execute(cfg, state, prices, markets, signals, halt, now)
    events = [{"kind": "close", **tr} for tr in trades] + events  # stops hit before the cycle too
    trades += closed
    state["last_summary"] = summary
    state["last_cycle"] = now.strftime(FMT)
    journal.log_calls(rows)
    journal.log_trades(trades)
    state["evaluation"] = report.evaluation(cfg, state, prices, closed_all, journal.read_calls(),
                                            journal.read_trades(), now)
    account.save_state(state)
    eq = account.equity(cfg, state, prices)
    print(f"  equity {eq:.2f} EUR (start {state['start_eur']:.2f}), {len(state['positions'])} open")
    ha.publish(cfg, state, prices, events, rows)
