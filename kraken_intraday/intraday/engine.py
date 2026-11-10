"""The state (two books on one set of candles) and the hourly step.

A book is a simulated CFD account in the CFD paper add-on's format (balance, positions, costs, peak equity,
drawdown halt) plus the day's counters and the equity at the end of every day.

The hourly step, for every pair of a book, in this order:
1. an open position is closed when it has been open max_hold_hours (TIME), when its market closes for the
   weekend within the hour (WEEKEND, flat_before_weekend), or when today's regime no longer allows it
   (REGIME); otherwise a breakout position's stop may trail,
2. without a position a new one is looked for, unless something blocks it: the regime (no_trade, out of date,
   confidence below min_confidence), the market hours, the drawdown halt, the daily loss limit, the
   trades-per-day limit, a high-impact US release within the blackout window, or no recent hourly candle,
3. the strategy's signal (strategies.signal) and the size: risk_per_trade_pct of equity between the entry
   and the stop, capped by the margin limits (the CFD paper rules).
One position per pair and book; a pair closed in a step is not reopened in the same step.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from cfd import account
from cfd import risk as cfd_risk
from krakenbot import macro

from . import regime, settings, strategies

FMT = "%Y-%m-%d %H:%M"
BOOKS = ("model", "rules")
KEEP_DAYS = 400  # days of end-of-day equity kept per book


def new_book(cfg, now):
    book = account.new_state(cfg, now)
    book.update(day=None, equity_by_day={})
    for key in ("first_prices", "started"):  # kept once, in the state
        book.pop(key, None)
    return book


def new_state(cfg, now=None):
    now = now or datetime.now()
    return {"books": {name: new_book(cfg, now) for name in BOOKS}, "regimes": {name: {} for name in BOOKS},
            "first_prices": {}, "started": now.strftime(FMT), "start_eur": float(cfg["start_eur"]),
            "last_hour_t": 0, "last_step": None}


def load_state(cfg):
    try:
        return json.loads(settings.STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return new_state(cfg)


def save_state(state):
    path = settings.STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # never a half-written state


# --- what blocks or ends a trade ---------------------------------------------------------------------------

def regime_of(cfg, state, book_name, pair, now):
    """(the regime as set, or None when missing or out of date; the regime the step trades by; why it is
    no_trade, or None)."""
    r = (state.get("regimes") or {}).get(book_name, {}).get(pair)
    if not r:
        return None, "no_trade", "no regime yet"
    age = (now - datetime.strptime(r["date"], FMT)).total_seconds() / 3600
    if age > cfg["intraday"]["regime_max_age_hours"]:
        return None, "no_trade", f"the regime of {r['date']} is out of date"
    conf, min_conf = r.get("confidence"), cfg["risk"]["min_confidence"]
    if conf is not None and conf < min_conf and r["regime"] != "no_trade":
        return r["regime"], "no_trade", f"{r['regime']} with confidence {conf:.2f} < {min_conf}"
    return r["regime"], r["regime"], ("regime no_trade" if r["regime"] == "no_trade" else None)


def blackout(cfg, state, now):
    """The high-impact US release (from the cached macro calendar) within blackout_before_min before or
    blackout_after_min after `now` (naive local time), or None."""
    p = cfg["intraday"]
    before, after = p.get("blackout_before_min", 0) / 60, p.get("blackout_after_min", 0) / 60
    events = (state.get("macro_calendar_cache") or {}).get("events")
    if not events or (before <= 0 and after <= 0):
        return None
    for e in macro.calendar_events(events, now.astimezone(timezone.utc), past_hours=after):
        if -after <= e["in_hours"] <= before:
            return e
    return None


def closes_soon(cfg, pair, now, minutes=60):
    """True when the instrument's market is open now and closed (the weekend) `minutes` from now."""
    return account.market_open(cfg, pair, now) and not account.market_open(cfg, pair, now + timedelta(minutes=minutes))


def exit_reason(cfg, pos, raw_regime, pair, now):
    if now.timestamp() - pos["opened_ts"] >= cfg["intraday"]["max_hold_hours"] * 3600:
        return "TIME"
    if cfg["intraday"].get("flat_before_weekend", True) and closes_soon(cfg, pair, now):
        return "WEEKEND"
    if raw_regime and (pos.get("strategy"), pos["side"]) not in regime.allowed(cfg, raw_regime):
        return "REGIME"
    return None


def entry_block(cfg, book, pair, candles, prices, now, halt, event):
    p = cfg["intraday"]
    if not account.market_open(cfg, pair, now):
        return "market closed"
    if p.get("flat_before_weekend", True) and closes_soon(cfg, pair, now):
        return "the market closes for the weekend within the hour"
    if halt:
        return halt
    day = book["day"]
    eq = account.equity(cfg, book, prices)
    loss = (1 - eq / day["start_equity"]) * 100 if day["start_equity"] else 0
    if loss >= p["max_daily_loss_pct"]:
        return f"daily loss {loss:.1f}% >= max_daily_loss_pct {p['max_daily_loss_pct']}"
    if day["trades"].get(pair, 0) >= p["max_trades_per_day"]:
        return f"{p['max_trades_per_day']} trades today already"
    if event:
        return f"blackout: {event['event']} at {event['time_utc']} UTC"
    if not candles or candles[-1]["t"] < now.timestamp() - 3 * 3600:
        return "no recent hourly candle"
    return None


# --- the step ----------------------------------------------------------------------------------------------

def roll_day(cfg, book, prices, now):
    """The day's counters: the equity at the start of the day and the trades opened per pair."""
    today = now.strftime("%Y-%m-%d")
    if (book.get("day") or {}).get("date") != today:
        book["day"] = {"date": today, "start_equity": round(account.equity(cfg, book, prices), 2), "trades": {}}
    return book["day"]


def annotate(row, book_name, pos):
    """Adds the intraday fields to a closed trade's row (account.close_position knows only the CFD ones)."""
    row.update(book=book_name, strategy=pos.get("strategy"), regime=pos.get("regime"),
               hours=round(float(row.get("days") or 0) * 24, 1))
    return row


def close(cfg, book, book_name, pair, price, reason, now, prices):
    pos = book["positions"][pair]
    return annotate(account.close_position(cfg, book, pair, price, reason, now, prices), book_name, pos)


def step(cfg, state, book_name, candles, prices, now):
    """One hourly step for one book. `candles`: {pair: closed hourly candles, oldest first}. Returns (closed
    trades' rows, open events, {pair: what happened})."""
    book = state["books"][book_name]
    roll_day(cfg, book, prices, now)
    halt = cfd_risk.check_halt(cfg, book, prices, now)
    event = blackout(cfg, state, now)
    rows, events, status = [], [], {}
    for pair in cfg["pairs"]:
        cs = candles.get(pair) or []
        raw, current, why_none = regime_of(cfg, state, book_name, pair, now)
        pos = book["positions"].get(pair)
        if pos:
            reason = exit_reason(cfg, pos, raw, pair, now)
            if reason:
                row = close(cfg, book, book_name, pair, account.price_of(prices, pair, pos), reason, now, prices)
                rows.append(row)
                status[pair] = f"closed {row['strategy']} {row['side']} ({reason}): {row['net_eur']:+} EUR"
                continue
            moved = strategies.trail(cfg, pos, cs[-1]["c"], strategies.hourly_atr(cfg, cs)) if cs else None
            pos["last_price"] = prices.get(pair, pos["last_price"])
            status[pair] = f"holding {pos.get('strategy')} {pos['side']}" + (f", stop trailed to {moved:g}" if moved else "")
            continue
        price = prices.get(pair)
        blocked = why_none or (entry_block(cfg, book, pair, cs, prices, now, halt, event) if price else "no price")
        if blocked:
            status[pair] = blocked
            continue
        prop, note = strategies.signal(cfg, pair, current, cs, price)
        if not prop:
            status[pair] = note
            continue
        qty, why = cfd_risk.size(cfg, book, prices, pair, price, prop["dist"])
        if not qty:
            status[pair] = f"{prop['why']} -> not opened: {why}"
            continue
        pos = account.open_position(cfg, book, pair, prop["side"], qty, price, prop["stop"], prop["take_profit"],
                                    now, prices)
        pos.update(strategy=prop["strategy"], regime=current, risk_dist=prop["dist"], best=price)
        book["day"]["trades"][pair] = book["day"]["trades"].get(pair, 0) + 1
        events.append({"kind": "open", "book": book_name, "pair": pair, "side": prop["side"],
                       "strategy": prop["strategy"], "qty": qty, "price": pos["entry"], "stop": pos["stop"],
                       "take_profit": pos["take_profit"], "why": prop["why"]})
        status[pair] = f"opened: {prop['why']}, stop {pos['stop']:g}, take-profit {pos['take_profit']:g}"
    return rows, events, status


def snapshot(cfg, state, prices, now):
    """Every book's equity as the day's (last) value."""
    today = now.strftime("%Y-%m-%d")
    for book in state["books"].values():
        days = book.setdefault("equity_by_day", {})
        days[today] = round(account.equity(cfg, book, prices), 2)
        for old in sorted(days)[:-KEEP_DAYS]:
            del days[old]
