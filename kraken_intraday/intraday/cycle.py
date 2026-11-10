"""Command line, the daily cycle and the refresh.

  python intraday_bot.py               # the daily cycle: data, the regimes (rule and model), an hourly step
  python intraday_bot.py --offline     # the same on fake data with random regimes (no internet, no API key)
  python intraday_bot.py --refresh     # stops / take-profits, swap, stop-out; the hourly step after a new candle
  python intraday_bot.py --report      # the latest evaluation
  python intraday_bot.py --backtest    # the strategies on the last 720 hourly candles (or --csv PAIR=FILE)
"""
import argparse
from datetime import datetime, timedelta, timezone

from cfd import account, guard
from cfd import cycle as cfd_cycle
from krakenbot import costs, i18n as kb_i18n, market

from . import backtest, engine, ha, journal, model, regime, report, settings, ta, texts

FMT = "%Y-%m-%d %H:%M"
HOURS_DAYS = 12  # hourly candles fetched for the strategies (288: plenty for the 50-hour EMA)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Intraday paper simulator (no real orders)")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--csv", action="append", default=[], metavar="PAIR=FILE",
                    help="hourly candles for --backtest (Kraken OHLCVT, the 60-minute file)")
    args = ap.parse_args(argv)
    cfg = settings.load_config()
    texts.set_language(cfg)
    kb_i18n.set_language(cfg)  # messages of the shared market-data code
    if args.backtest:
        backtest.run(cfg, args.csv, args.offline)
        return
    state = engine.load_state(cfg)
    if args.report:
        print(report.report_text(state))
    elif args.refresh:
        refresh(cfg, state)
    else:
        daily_cycle(cfg, state, offline_mode=args.offline)


# --- data --------------------------------------------------------------------------------------------------

def fetch_prices(cfg, state):
    prices = {pair: market.fetch_price(pair) for pair in cfg["pairs"]}
    for book in state["books"].values():  # a pair removed from config.json is still valued
        for pair, pos in book["positions"].items():
            if pair not in prices:
                prices[pair] = market.optional(f"price {pair}", market.fetch_price, pair) or pos["last_price"]
    if "EURUSD" not in prices and any(account.instrument(cfg, p)["quote"] == "USD" for p in prices):
        prices["EURUSD"] = market.fetch_price("EURUSD")  # to value USD-quoted pairs in EUR
    return prices


def load_hours(cfg, offline_mode=False, prices=None):
    """{pair: closed hourly candles, oldest first}. A pair whose candles cannot be fetched is left out (no
    entries for it this hour)."""
    if offline_mode:
        return {pair: backtest.fake_hours((prices or {}).get(pair) or 100.0, n=HOURS_DAYS * 24, seed=i)
                for i, pair in enumerate(cfg["pairs"])}
    out = {}
    for pair in cfg["pairs"]:
        closed = market.optional(f"hourly {pair}", lambda p=pair: market.fetch_ohlc(p, HOURS_DAYS, 60)[0])
        if closed:
            out[pair] = closed
    return out


def latest_hour(candles):
    return max((cs[-1]["t"] for cs in candles.values() if cs), default=0)


def hour_due(state, now):
    """True once the hourly candle after the last processed one has closed (a minute of margin for Kraken)."""
    last = state.get("last_hour_t") or 0
    return now.timestamp() >= last + 2 * 3600 + 60


def cached_fetch(fetch, state):
    """guard.fetch_candles for both books with one request per pair: from the earliest moment any open
    position on the pair still needs checking."""
    if fetch is None:
        return None
    earliest = {}
    for book in state["books"].values():
        for pair, pos in book["positions"].items():
            since = max(pos["opened_ts"], book.get("last_check") or 0)
            earliest[pair] = min(earliest.get(pair, since), since)
    cache = {}

    def get(pair, since, now):
        if pair not in cache:
            cache[pair] = fetch(pair, min(since, earliest.get(pair, since)), now)
        return cache[pair]
    return get


def protect_all(cfg, state, prices, now, fetch=guard.fetch_candles):
    """Stops / take-profits since the last check, the overnight swap and the margin stop-out, for both books.
    Returns the closed trades."""
    get = cached_fetch(fetch, state)
    rows = []
    for name in engine.BOOKS:
        book = state["books"][name]
        before = {pair: dict(pos) for pair, pos in book["positions"].items()}
        rows += [engine.annotate(r, name, before.get(r["pair"], {}))
                 for r in cfd_cycle.protect(cfg, book, prices, now, fetch=get)]
    state["last_prices"] = dict(prices)
    state["last_refresh"] = now.strftime(FMT)
    return rows


def run_steps(cfg, state, candles, prices, now):
    """The hourly step for both books. Returns (closed trades, events)."""
    rows, events = [], []
    state["last_step"] = {"date": now.strftime(FMT), "books": {}}
    for name in engine.BOOKS:
        closed, opened, status = engine.step(cfg, state, name, candles, prices, now)
        rows += closed
        events += [{"kind": "close", **r} for r in closed] + opened
        state["last_step"]["books"][name] = status
        for pair, what in status.items():
            print(f"  [{name}] {pair}: {what}")
    latest = latest_hour(candles)
    if latest:
        state["last_hour_t"] = max(state.get("last_hour_t") or 0, latest)
    return rows, events


def finish(cfg, state, prices, trades, events, now, daily=False):
    journal.log_trades(trades)
    engine.snapshot(cfg, state, prices, now)
    state["evaluation"] = report.evaluation(cfg, state, prices, journal.read_regimes(), journal.read_trades(), now)
    engine.save_state(state)
    ha.publish(cfg, state, prices, events, daily=daily)


def refresh(cfg, state, now=None, fetch=guard.fetch_candles, hours=None):
    """Between daily cycles (every few minutes): protection for both books, and the hourly step once a new
    hourly candle has closed. `hours`: the candle loader (tests)."""
    now = now or datetime.now()
    prices = fetch_prices(cfg, state)
    trades = protect_all(cfg, state, prices, now, fetch)
    events = [{"kind": "close", **tr} for tr in trades]
    if hour_due(state, now):
        candles = (hours or load_hours)(cfg)
        if latest_hour(candles) > (state.get("last_hour_t") or 0):
            print(f"=== intraday paper {now:%Y-%m-%d %H:%M}: hourly step ===")
            rows, ev = run_steps(cfg, state, candles, prices, now)
            trades += rows
            events += ev
    finish(cfg, state, prices, trades, events, now)


# --- the daily cycle ---------------------------------------------------------------------------------------

def hourly_view(cfg, pair, candles, price):
    """The hourly picture the rules work on, for the model."""
    if not candles or len(candles) < 72:
        return None
    closes = [c["c"] for c in candles]
    a = ta.atr(candles, cfg["intraday"]["atr_hours"])
    lower, mid, upper = ta.bands(closes, 20, 2.0)
    ema50 = ta.ema(closes, 50)
    strength = ta.adx(candles[-120:], 14)
    atr_pct = a / price * 100
    spread = account.spread_pct(cfg, pair)
    return {"atr_pct": round(atr_pct, 3), "adx14": round(strength, 1) if strength is not None else None,
            "rsi14": round(ta.rsi(closes, 14), 1), "pct_vs_ema50": round((price / ema50 - 1) * 100, 2),
            "band_width_pct": round((upper - lower) / mid * 100, 2),
            "change_24h_pct": round((price / closes[-24] - 1) * 100, 2),
            "change_72h_pct": round((price / closes[-72] - 1) * 100, 2),
            "round_trip_spread_pct": spread, "spread_in_hourly_atrs": round(spread / atr_pct, 2) if atr_pct else None}


def trading_terms(cfg, now):
    instruments = {}
    for pair in cfg["pairs"]:
        inst = account.instrument(cfg, pair)
        instruments[pair] = {
            "name": inst["name"], "asset_class": inst["asset_class"], "price_in": inst["quote"],
            "leverage": inst["leverage"], "round_trip_spread_pct": inst["spread_pct"],
            "overnight_swap_pct_per_day": inst["swap_pct_per_day"],
            "trading_hours": (f"closed {inst['weekend_close']} - {inst['weekend_open']} (local time)"
                              if inst["weekend_close"] else "24/7"),
            "market_open_now": account.market_open(cfg, pair, now)}
    i, r = cfg["intraday"], cfg["risk"]
    # the model is not told the account is simulated: it should judge it like a real one
    return {"account_currency": "EUR", "allow_short": cfg.get("allow_short", True), "instruments": instruments,
            "strategies": {name: p for name, p in cfg["strategies"].items() if p.get("enabled", True)},
            "min_confidence": r["min_confidence"], "risk_per_trade_pct": r["risk_per_trade_pct"],
            **{k: i[k] for k in ("min_target_to_cost", "max_hold_hours", "max_trades_per_day", "max_daily_loss_pct",
                                 "blackout_before_min", "blackout_after_min")}}


def recent_days(state, regime_rows, trades, now, days=5):
    """The model book's regimes of the last days, the move until the next one, and the trades they produced."""
    since = (now - timedelta(days=days)).strftime(FMT)
    rows = sorted((r for r in regime_rows if r["book"] == "model"), key=lambda r: r["date"])
    out = []
    for i, r in enumerate(rows):
        if r["date"] < since:
            continue
        nxt = next((x for x in rows[i + 1:] if x["pair"] == r["pair"]), None)
        price_then, price_next = report.num(r["price"]), report.num(nxt["price"]) if nxt else None
        moved = round((price_next / price_then - 1) * 100, 2) if price_then and price_next else None
        mine = [t for t in trades if t.get("book") == "model" and t["pair"] == r["pair"]
                and r["date"] <= t["opened"] < (nxt["date"] if nxt else "9999")]
        out.append({"date": r["date"], "pair": r["pair"], "regime": r["regime"], "confidence": r["confidence"],
                    "move_until_next_pct": moved,
                    "trades": [{k: t[k] for k in ("strategy", "side", "reason", "net_eur", "hours")} for t in mine]})
    return out


def account_view(cfg, book, prices):
    return {"equity_eur": round(account.equity(cfg, book, prices), 2),
            "positions": {pair: {"side": pos["side"], "strategy": pos.get("strategy"), "entry": round(pos["entry"], 6),
                                 "stop": pos["stop"], "take_profit": pos["take_profit"],
                                 "unrealized_eur": round(account.unrealized(cfg, pair, pos, account.price_of(prices, pair, pos), prices), 2)}
                          for pair, pos in book["positions"].items()}}


def daily_cycle(cfg, state, offline_mode=False, now=None, ask=None, hours=None):
    now = now or datetime.now()
    pairs = cfg["pairs"]
    print(f"=== intraday paper {now:%Y-%m-%d %H:%M}: {', '.join(pairs)}{' (OFFLINE)' if offline_mode else ''} ===")
    crypto = [pair for pair in pairs if account.is_crypto(cfg, pair)]
    markets, prices, closed_all = cfd_cycle.load_markets(pairs, offline_mode, crypto)
    if not offline_mode and "EURUSD" not in prices and any(account.instrument(cfg, p)["quote"] == "USD" for p in pairs):
        prices["EURUSD"] = market.fetch_price("EURUSD")  # to value USD-quoted pairs in EUR
    for pair in pairs:
        state["first_prices"].setdefault(pair, prices[pair])
    trades = protect_all(cfg, state, prices, now, fetch=None if offline_mode else guard.fetch_candles)
    candles = hours(cfg) if hours else load_hours(cfg, offline_mode, prices)
    date = now.strftime(FMT)

    # the rule first: it needs no API, and the rules book keeps trading if the model call fails
    for pair in pairs:
        state["regimes"]["rules"][pair] = {**regime.rules(cfg, closed_all[pair]), "confidence": None, "date": date}
        markets[pair]["hourly"] = hourly_view(cfg, pair, candles.get(pair), prices[pair])
    engine.save_state(state)

    news = [] if offline_mode else market.fetch_news(cfg["news_feeds"])
    context = {} if offline_mode else cfd_cycle.load_context(crypto, state, closed_all)
    trades_log = journal.read_trades() + trades
    payload = {"now_utc": now.astimezone(timezone.utc).strftime(FMT), "markets": markets,
               "account": account_view(cfg, state["books"]["model"], prices),
               "trading_terms": trading_terms(cfg, now), "news_last_24h": news,
               "own_recent_days": recent_days(state, journal.read_regimes(), trades_log, now),
               "own_track_record": {"regimes": ((state.get("evaluation") or {}).get("regimes") or {}).get("model"),
                                    "trades_by_strategy": ((((state.get("evaluation") or {}).get("books") or {})
                                                            .get("model") or {}).get("trades") or {}).get("by_strategy")},
               **context}
    if offline_mode:
        regimes, summary = {pair: model.fake_regime() for pair in pairs}, "OFFLINE TEST"
    else:
        model.last_usage = None
        try:
            regimes, summary = (ask or model.ask)(cfg, payload)
        finally:  # a call is paid for even when the cycle fails afterwards
            if model.last_usage:
                cost = costs.record(cfg, state, model.last_usage, "daily")
                print(f"  model cost {cost:.3f} USD (this month {state['api_usage']['cost_usd']:.2f} USD)")
                engine.save_state(state)
    print(f"  model: {summary}")
    rows = []
    for pair in pairs:
        state["regimes"]["model"][pair] = {**regimes[pair], "date": date}
        for name in engine.BOOKS:
            r = state["regimes"][name][pair]
            rows.append({"date": date, "book": name, "pair": pair, "price": round(prices[pair], 6),
                         "regime": r["regime"], "confidence": r["confidence"],
                         "atr_pct": markets[pair].get("atr14_pct"), "reasoning": r["reasoning"]})
        model_r, rules_r = state["regimes"]["model"][pair], state["regimes"]["rules"][pair]
        print(f"  {pair}: model {model_r['regime']} ({model_r['confidence']:.2f}), rule {rules_r['regime']}"
              f" ({rules_r['reasoning']})")
        print(f"             {model_r['reasoning']}")
    journal.log_regimes(rows)
    state["last_summary"] = summary
    state["last_cycle"] = date

    closed, events = run_steps(cfg, state, candles, prices, now)  # the new regimes act at once
    events = [{"kind": "close", **tr} for tr in trades] + events
    finish(cfg, state, prices, trades + closed, events, now, daily=True)
    eq = account.equity(cfg, state["books"]["model"], prices)
    print(f"  model book {eq:.2f} EUR, rules book {account.equity(cfg, state['books']['rules'], prices):.2f} EUR"
          f" (start {state['start_eur']:.2f})")
