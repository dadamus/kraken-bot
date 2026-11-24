"""Live mode: the model's calls traded on Kraken Derivatives with real money.

It needs two opt-ins (the add-on option execution: live AND "mode": "live" in config.json), a derivatives key
with "Full Access" (never withdrawal rights) and a tradable EEA contract for every pair in "pairs" (bitcoin
PF_XBTUSD, gold PF_PAXGUSD; there is no EUR/USD for EEA clients - remove it from "pairs"). Live has its own
files (state_live.json, calls_live.csv, trades_live.csv); the paper account's are left as they are.

The exchange is the source of truth:
- every run reads the wallet, the positions and the resting orders. The return is measured from the
  wallet's value at the first live run; a transfer into or out of the derivatives wallet moves that
  reference instead of counting as profit or loss,
- a position opens with a market order (Kraken gives market orders 1 % price protection), then a stop and a
  take-profit rest on Kraken as reduce-only orders triggered on the mark price (one trade in a thin book
  cannot fire them). A position whose stop cannot be placed is closed at once: never leverage without a stop,
- the stop moves (the model's tighter stop, break-even) are edits of the resting order,
- a position gone from the exchange was closed there (stop, take-profit, liquidation): its fills give the
  exit, the public funding history the funding over the holding time,
- the bot's own rules stay: the margin stop-out at stop_out_margin_level_pct, the drawdown halt, the risk
  limits; the leverage is the lower of config.json's and Kraken's EEA limit,
- switching execution away from live while positions are open does not abandon them: the bot keeps their
  stops on Kraken and records their closes (watch), it just opens nothing new.

Prices: the model, the stops in state and the risk rules work in the spot pair's prices (EUR); every order
carries a level over to the contract's USD price at the same distance in % (venue.carry).
"""
import copy
import json
from datetime import datetime

from krakenbot import alerts, market

from . import account, futures, guard, journal, settings, sources, venue

FMT = "%Y-%m-%d %H:%M"
TRIGGER = "mark"


class LiveError(RuntimeError):
    """Live mode cannot run (missing opt-in, key or contract)."""


def opposite(side):
    return "sell" if side == "long" else "buy"


def opening(side):
    return "buy" if side == "long" else "sell"


# --- start ----------------------------------------------------------------------------------------------

def check_ready(cfg, fetch_specs=None):
    """{symbol: spec} when live can run, else LiveError with the reason."""
    fetch_specs = fetch_specs or futures.instruments
    if cfg.get("mode") != "live":
        raise LiveError('execution is "live" but config.json has no "mode": "live" - real orders need both')
    if not futures.has_keys():
        raise LiveError("live mode needs a Kraken derivatives key (kraken_futures_api_key / _secret, Full Access)")
    wanted = [venue.symbol(cfg, pair) for pair in cfg["pairs"]]
    specs = {s: venue.trim(raw) for s, raw in fetch_specs(wanted).items()}
    missing = [f"{pair}: {venue.why_unavailable(specs.get(sym), sym)}" for pair, sym in zip(cfg["pairs"], wanted)
               if not venue.available(specs.get(sym))]
    if missing:
        raise LiveError("no tradable Kraken contract, remove the pair from \"pairs\": " + "; ".join(missing))
    return specs


def adapt(cfg, specs):
    """The instruments as Kraken trades them: leverage capped at the EEA limit, no paper swap (the funding is
    real and in the wallet's value), 24/7 hours, the taker fee on both sides as the round-trip cost."""
    insts = cfg.setdefault("instruments", {})
    for pair in cfg["pairs"]:
        spec = specs[venue.symbol(cfg, pair)]
        own = insts.setdefault(pair, {})
        lev = account.leverage(cfg, pair)
        own["leverage"] = min(lev, spec["max_leverage"]) if spec["max_leverage"] else lev
        own["swap_pct_per_day"] = {"long": 0.0, "short": 0.0}
        own["weekend_close"] = own["weekend_open"] = None
        own["spread_pct"] = 2 * venue.TAKER_FEE_PCT
        own["max_wick"] = own.get("max_wick", 0)


def prepare(cfg, fetch_specs=None):
    """Everything live needs before the state is loaded. Returns the Broker."""
    specs = check_ready(cfg, fetch_specs)
    adapt(cfg, specs)
    settings.use_live_files()
    futures.ALLOW = "all"
    return Broker(specs)


# --- the trade row --------------------------------------------------------------------------------------

def funding_eur(sym, pos, closed_ts, eurusd, history=None):
    """Funding a position paid over its holding time (negative: received), from Kraken's public hourly rates."""
    try:
        rates = (history or futures.funding_history)(sym)
    except Exception as e:
        print(f"  [kraken] funding history {sym} unavailable: {e}")
        return 0.0
    rate = sum(r for t, r in rates if pos["opened_ts"] <= t < closed_ts)
    sign = 1 if pos["side"] == "long" else -1
    return pos["size"] * pos["entry_usd"] * rate * sign / eurusd


def trade_row(cfg, state, pair, exit_usd, reason, now, eurusd, history=None):
    """Removes the position and returns its trades_live.csv row (in the contract's USD prices, money in EUR)."""
    pos = state["positions"].pop(pair)
    sign = 1 if pos["side"] == "long" else -1
    size, entry = pos["size"], pos["entry_usd"]
    gross = (exit_usd - entry) * size * sign / eurusd
    fee = (entry + exit_usd) * size * venue.TAKER_FEE_PCT / 100 / eurusd
    fund = funding_eur(pos["symbol"], pos, now.timestamp(), eurusd, history)
    net = gross - fee - fund
    state["balance"] += net  # until the next sync puts the wallet's value back in
    risk_eur = size * abs(entry - pos.get("initial_stop_usd", pos["stop_usd"])) / eurusd
    opened = datetime.strptime(pos["opened"], FMT)
    return {"opened": pos["opened"], "closed": now.strftime(FMT), "pair": pair, "side": pos["side"],
            "qty": size, "entry": entry, "exit": round(exit_usd, 6), "stop": pos["stop_usd"],
            "take_profit": pos.get("tp_usd"), "reason": reason,
            "move_pct": round((exit_usd / entry - 1) * 100 * sign, 2), "pnl_eur": round(gross, 2),
            "swap_eur": round(fund, 2), "spread_eur": round(fee, 2), "net_eur": round(net, 2),
            "r_multiple": round(net / risk_eur, 2) if risk_eur else None,
            "days": round(max(0.0, (now - opened).total_seconds()) / 86400, 2),
            "balance_eur": round(state["balance"], 2), "symbol": pos["symbol"], "size": size,
            "entry_contract": entry, "exit_contract": round(exit_usd, 6), "fee_eur": round(fee, 2),
            "funding_eur": round(fund, 2)}


# --- the broker -----------------------------------------------------------------------------------------

class Broker:
    """Real orders on Kraken Derivatives, with the paper broker's interface (cycle.PaperBroker). A failed
    order returns None and leaves the reason in self.error."""
    live = True

    def __init__(self, specs, history=None):
        self.specs, self.history, self.error, self.extra_trades = specs, history, None, []

    def spec(self, cfg, pair):
        return self.specs[venue.symbol(cfg, pair)]

    def _close_now(self, sym, side, size):
        """An emergency market close (reduce-only). Returns the average price or None."""
        try:
            return futures.executed(futures.send_order(sym, opposite(side), size, "mkt", reduce_only=True))[1]
        except Exception as e:
            print(f"  [kraken] emergency close {sym} failed: {e}")
            return None

    def open(self, cfg, state, pair, side, qty, mid, stop, take_profit, now, prices):
        self.error = None
        sym, spec = venue.symbol(cfg, pair), self.spec(cfg, pair)
        size = venue.round_size(spec, qty)
        if size < venue.min_size(spec):
            self.error = f"size {qty:.6g} below {sym}'s minimum {venue.min_size(spec):g}"
            return None
        try:
            contract_mid = venue.mid(futures.ticker(sym))
            status = futures.send_order(sym, opening(side), size, "mkt", cli_ord_id=f"cfd-{pair}-{int(now.timestamp())}")
        except Exception as e:
            self.error = f"order failed: {e}"
            alerts.problem(state, f"live_order_{pair}", f"CFD live: opening {side} {pair} failed: {e}")
            return None
        filled, price = futures.executed(status)
        if not filled:
            self.error = f"market order not filled ({status.get('status')})"
            return None
        size = venue.round_size(spec, filled)
        stop_usd = venue.round_price(spec, venue.carry(stop, mid, contract_mid))
        tp_usd = venue.round_price(spec, venue.carry(take_profit, mid, contract_mid))
        try:
            stop_order = futures.send_order(sym, opposite(side), size, "stp", stop_price=stop_usd, reduce_only=True,
                                            trigger_signal=TRIGGER)["order_id"]
        except Exception as e:
            exit_price = self._close_now(sym, side, size)
            msg = f"CFD live: the stop for {pair} could not be placed ({e}); the position was closed at once"
            if exit_price is None:
                msg = f"CFD live: the stop for {pair} could not be placed ({e}) AND closing failed - check Kraken now"
            alerts.problem(state, f"live_stop_{pair}", msg)
            self.error = msg
            if exit_price is not None:
                state["positions"][pair] = {"side": side, "size": size, "entry_usd": price, "stop_usd": stop_usd,
                                            "symbol": sym, "opened": now.strftime(FMT),
                                            "opened_ts": int(now.timestamp())}
                self.extra_trades.append(trade_row(cfg, state, pair, exit_price, "NO STOP", now,
                                                   eurusd(prices), self.history))
            return None
        try:
            tp_order = futures.send_order(sym, opposite(side), size, "take_profit", stop_price=tp_usd,
                                          reduce_only=True, trigger_signal=TRIGGER)["order_id"]
        except Exception as e:  # the stop protects; the take-profit is tried again at the next sync
            tp_order = None
            alerts.problem(state, f"live_tp_{pair}", f"CFD live: the take-profit for {pair} could not be placed: {e}")
        pos = account.open_position(cfg, state, pair, side, size, mid * price / contract_mid, stop, take_profit, now,
                                    prices)
        pos.update(symbol=sym, size=size, entry_usd=price, stop_usd=stop_usd, initial_stop_usd=stop_usd,
                   tp_usd=tp_usd, stop_order=stop_order, tp_order=tp_order)
        print(f"  [kraken] {opening(side)} {size:g} {sym} @ {price:g}, stop {stop_usd:g}, take-profit {tp_usd:g}")
        return pos

    def close(self, cfg, state, pair, mid, reason, now, prices):
        self.error = None
        pos = state["positions"][pair]
        try:
            status = futures.send_order(pos["symbol"], opposite(pos["side"]), pos["size"], "mkt", reduce_only=True)
        except Exception as e:
            self.error = f"close failed: {e}"
            alerts.problem(state, f"live_order_{pair}", f"CFD live: closing {pair} failed: {e} (its stop stays)")
            return None
        filled, price = futures.executed(status)
        if not filled:
            self.error = f"close not filled ({status.get('status')}); the stop stays"
            return None
        for order_id in (pos.get("stop_order"), pos.get("tp_order")):
            if order_id:
                futures.cancel_order(order_id)
        print(f"  [kraken] closed {pos['side']} {pos['size']:g} {pos['symbol']} @ {price:g} ({reason})")
        return trade_row(cfg, state, pair, price, reason, now, eurusd(prices), self.history)

    def move_stop(self, cfg, state, pair, now, prices, breakeven=False):
        """Carries the position's (already moved) spot stop over to the resting order. Break-even goes to the
        entry price plus the fees of both sides."""
        pos = state["positions"][pair]
        spec = self.spec(cfg, pair)
        sign = 1 if pos["side"] == "long" else -1
        try:
            if breakeven:
                level = pos["entry_usd"] * (1 + sign * 2 * venue.TAKER_FEE_PCT / 100)
            else:
                level = venue.carry(pos["stop"], prices[pair], venue.mid(futures.ticker(pos["symbol"])))
            level = venue.round_price(spec, level)
            if (level - pos["stop_usd"]) * sign <= 0:  # never loosened
                return None
            futures.edit_order(pos["stop_order"], level)
        except Exception as e:
            alerts.problem(state, f"live_stop_move_{pair}", f"CFD live: moving the stop of {pair} failed: {e}")
            return None
        alerts.problem(state, f"live_stop_move_{pair}")
        print(f"  [kraken] {pair} stop {pos['stop_usd']:g} -> {level:g}{' (break-even)' if breakeven else ''}")
        pos["stop_usd"] = level
        return level

    # --- between the cycles: the exchange's view of things ------------------------------------------------

    def protect(self, cfg, state, prices, now, fetch=guard.fetch_candles):
        return sync(cfg, state, prices, now, self, fetch)


def eurusd(prices):
    if not prices.get("EURUSD"):
        prices["EURUSD"] = market.fetch_price("EURUSD")
    return prices["EURUSD"]


def cash_flows(state, rate, now):
    """Transfers into / out of the derivatives wallet since the last run move the return reference (the same
    rule as the spot bot's deposits). Kraken's account log names them "... transfer"."""
    since = state.get("log_checked_ms")
    entries = futures.account_log(since)
    latest = since or 0
    for e in entries:
        when = futures.utc_ts(e.get("date") or "1970-01-01T00:00:00") * 1000
        latest = max(latest, when)
        if since and when <= since:
            continue
        info = str(e.get("info") or "").lower()
        if "transfer" not in info:
            continue
        delta = float(e.get("new_balance") or 0) - float(e.get("old_balance") or 0)
        asset = str(e.get("asset") or "").upper()
        eur = delta if asset == "EUR" else delta / rate if asset in ("USD", "USDC") else None
        if eur is None:
            print(f"  [kraken] transfer of {delta:g} {asset} not counted (only EUR / USD / USDC)")
            continue
        state["start_eur"] = round(state["start_eur"] + eur, 2)
        state["peak_equity"] = (state.get("peak_equity") or 0) + eur
        state.setdefault("cash_flows", []).append({"time": now.strftime(FMT), "info": info, "asset": asset,
                                                   "amount": delta, "eur": round(eur, 2)})
        print(f"  [kraken] {info}: {delta:+g} {asset} ({eur:+.2f} EUR) - return reference {state['start_eur']:.2f}")
    state["log_checked_ms"] = latest


def closed_on_exchange(cfg, state, pair, pos, prices, now, broker, orders):
    """The position is gone from Kraken: its exit from the fills (stop, take-profit, liquidation)."""
    sym, side = pos["symbol"], opposite(pos["side"])
    mine = [f for f in futures.fills() if str(f.get("symbol")).upper() == sym and f.get("side") == side
            and futures.utc_ts(f["fillTime"]) >= pos["opened_ts"]]
    size = sum(float(f["size"]) for f in mine)
    price = sum(float(f["size"]) * float(f["price"]) for f in mine) / size if size else None
    ids = {f.get("order_id") for f in mine}
    kinds = {str(f.get("fillType") or "").lower() for f in mine}
    reason = ("LIQUIDATION" if "liquidation" in kinds else "STOP" if pos.get("stop_order") in ids
              else "TAKE-PROFIT" if pos.get("tp_order") in ids else "CLOSED ON KRAKEN")
    for order_id in (pos.get("stop_order"), pos.get("tp_order")):
        if order_id and order_id in orders:
            futures.cancel_order(order_id)
    if price is None:
        price = pos["stop_usd"]
        alerts.problem(state, f"live_unknown_exit_{pair}",
                       f"CFD live: {pair} is closed on Kraken but no fill was found; recorded at its stop")
    return trade_row(cfg, state, pair, price, reason, now, eurusd(prices), broker.history)


def ensure_orders(cfg, state, pair, pos, orders, broker):
    """A stop or take-profit missing from the book is placed again; no stop possible -> closed at once."""
    sym, spec = pos["symbol"], broker.spec(cfg, pair)
    if pos.get("stop_order") not in orders:
        try:
            pos["stop_order"] = futures.send_order(sym, opposite(pos["side"]), pos["size"], "stp",
                                                   stop_price=pos["stop_usd"], reduce_only=True,
                                                   trigger_signal=TRIGGER)["order_id"]
            print(f"  [kraken] {pair}: the stop was missing, placed again at {pos['stop_usd']:g}")
        except Exception as e:
            return f"stop missing and cannot be placed: {e}"
    if pos.get("tp_usd") and pos.get("tp_order") not in orders:
        try:
            pos["tp_order"] = futures.send_order(sym, opposite(pos["side"]), pos["size"], "take_profit",
                                                 stop_price=venue.round_price(spec, pos["tp_usd"]),
                                                 reduce_only=True, trigger_signal=TRIGGER)["order_id"]
            alerts.problem(state, f"live_tp_{pair}")
        except Exception as e:
            alerts.problem(state, f"live_tp_{pair}", f"CFD live: the take-profit for {pair} could not be placed: {e}")
    return None


def sync(cfg, state, prices, now, broker, fetch=guard.fetch_candles):
    """The exchange's state into the bot's: wallet, positions, orders, closes; break-even and the margin
    stop-out. Returns the closed trades."""
    rate = eurusd(prices)
    flex = futures.flex_account()
    value = float(flex.get("portfolioValue") or 0) / rate
    if not state.get("live_synced"):
        state.update(start_eur=round(value, 2), peak_equity=value, balance=value, live_synced=now.strftime(FMT),
                     started=now.strftime(FMT), log_checked_ms=now.timestamp() * 1000)
        print(f"  [kraken] first live run: derivatives wallet {value:,.2f} EUR is the reference")
    else:
        try:
            cash_flows(state, rate, now)
        except Exception as e:
            print(f"  [kraken] account log unavailable: {e}")
    exchange = {str(p["symbol"]).upper(): p for p in futures.open_positions()}  # Kraken may answer in lowercase
    orders = {o.get("order_id"): o for o in futures.open_orders()}
    rows = []
    for pair in list(state["positions"]):
        pos = state["positions"][pair]
        ex = exchange.get(pos["symbol"])
        if not ex:
            rows.append(closed_on_exchange(cfg, state, pair, pos, prices, now, broker, orders))
            continue
        size = float(ex.get("size") or 0)
        if abs(size - pos["size"]) > 1e-9:  # a partial liquidation, or a hand on the Kraken app
            alerts.problem(state, f"live_size_{pair}", f"CFD live: {pair} is {size:g} on Kraken, the bot had"
                           f" {pos['size']:g} - taken over")
            pos["size"] = pos["qty"] = size
        problem = ensure_orders(cfg, state, pair, pos, orders, broker)
        if problem:
            exit_price = broker._close_now(pos["symbol"], pos["side"], pos["size"])
            alerts.problem(state, f"live_stop_{pair}", f"CFD live: {pair} {problem}; "
                           + ("closed at once" if exit_price else "closing FAILED - check Kraken now"))
            if exit_price:
                rows.append(trade_row(cfg, state, pair, exit_price, "NO STOP", now, rate, broker.history))
    known = {pos["symbol"] for pos in state["positions"].values()} | {r["symbol"] for r in rows}
    for sym in exchange:
        key = f"live_unknown_{sym}"
        if sym not in known:
            alerts.problem(state, key, f"CFD live: a {sym} position on Kraken the bot did not open - left alone")
        else:
            alerts.problem(state, key)
    breakeven(cfg, state, prices, now, broker, fetch)
    unreal = sum(account.unrealized(cfg, p, pos, account.price_of(prices, p, pos), prices)
                 for p, pos in state["positions"].items())
    state["balance"] = value - unreal  # account.equity() = the wallet's value
    rows += stop_out(cfg, state, prices, now, broker)
    return rows


def breakeven(cfg, state, prices, now, broker, fetch=guard.fetch_candles):
    """guard.breakeven on the spot candles since the last check; a move edits the resting stop."""
    for pair, pos in state["positions"].items():
        since = max(pos["opened_ts"], state.get("last_check") or 0)
        candles = []
        if fetch and since < now.timestamp():
            try:
                candles, interval = fetch(pair, since, now)
                candles = [c for c in account.clean_candles(cfg, pair, candles) if c["t"] + interval * 60 > since]
            except Exception as e:
                print(f"  [{pair}] candles for break-even unavailable: {e}")
        if pair in prices:
            p = prices[pair]
            candles.append({"t": int(now.timestamp()), "o": p, "h": p, "l": p, "c": p})
        for candle in candles:
            if guard.breakeven(cfg, pair, pos, candle):
                broker.move_stop(cfg, state, pair, now, prices, breakeven=True)
                break


def stop_out(cfg, state, prices, now, broker):
    """The bot's own margin stop-out (stop_out_margin_level_pct), on the wallet's value: Kraken liquidates far
    later (below the maintenance margin), so this rule is kept from the paper account."""
    limit = float(cfg.get("stop_out_margin_level_pct", 50))
    rows = []
    while state["positions"]:
        level = account.margin_level(cfg, state, prices)
        if level is None or level >= limit:
            break
        worst = min(state["positions"], key=lambda p: account.unrealized(
            cfg, p, state["positions"][p], account.price_of(prices, p, state["positions"][p]), prices))
        row = broker.close(cfg, state, worst, prices.get(worst), "STOP-OUT", now, prices)
        if not row:
            break
        rows.append(row)
    return rows


# --- winding down: live positions while execution is no longer live -------------------------------------

def watch(cfg, now=None, fetch_specs=None):
    """Keeps the live positions protected and records their closes while execution is not "live": nothing new
    is opened, only orders that reduce a position may be sent. Returns the closed trades."""
    path = settings.LIVE_STATE_FILE
    if not path.exists() or not futures.has_keys():
        return []
    state = json.loads(path.read_text(encoding="utf-8"))
    if not state.get("positions"):
        return []
    now = now or datetime.now()
    print(f"  [kraken] {len(state['positions'])} live position(s) still open: watched until closed")
    allow, futures.ALLOW = futures.ALLOW, "reduce"
    try:
        fetch_specs = fetch_specs or futures.instruments
        specs = {s: venue.trim(raw) for s, raw in fetch_specs([p["symbol"] for p in state["positions"].values()]).items()}
        live_cfg = copy.deepcopy(cfg)
        live_cfg["pairs"] = list(state["positions"])
        for pair, pos in state["positions"].items():
            live_cfg.setdefault("instruments", {}).setdefault(pair, {})["futures_symbols"] = [pos["symbol"]]
        adapt(live_cfg, specs)
        sources.use(live_cfg)
        prices = {pair: sources.price(pair) for pair in state["positions"]}
        broker = Broker(specs)
        rows = sync(live_cfg, state, prices, now, broker, None) + broker.extra_trades
        alerts.problem(state, "live_watch", "CFD: live positions are open while execution is not \"live\" - their"
                       " stops stay on Kraken, nothing new is opened")
        if not state["positions"]:
            alerts.problem(state, "live_watch")
        journal.append(settings.LIVE_TRADES_FILE, journal.LIVE_TRADE_FIELDS, rows)
        alerts.flush(cfg, state, now)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return rows
    finally:
        futures.ALLOW = allow


# --- the one-off test with real money ---------------------------------------------------------------------

def smoke_test(sym="PF_XBTUSD", log=print, fetch_specs=None):
    """One minimum-size round trip with every order the live mode uses: market buy, resting stop and
    take-profit (reduce-only, mark price), a stop edit, cancels, a reduce-only market sell. Run once, by the
    user, before switching live on (about 5 EUR notional, fees of a few cents). Returns True when it all worked;
    whatever happens, it tries to leave no position and no order behind."""
    allow, futures.ALLOW = futures.ALLOW, "all"
    ids, bought, ok = [], 0.0, False
    try:
        flex = futures.flex_account()
        log(f"key and signing work: wallet {float(flex.get('portfolioValue') or 0):,.2f} USD,"
            f" free margin {float(flex.get('availableMargin') or 0):,.2f} USD")
        spec = venue.trim((fetch_specs or futures.instruments)([sym])[sym])
        if not venue.available(spec):
            log(f"FAILED: {venue.why_unavailable(spec, sym)}")
            return False
        if any(p.get("symbol") == sym for p in futures.open_positions()):
            log(f"FAILED: there is already a {sym} position on the account - the test does not touch it")
            return False
        size = venue.min_size(spec)
        m = venue.mid(futures.ticker(sym))
        bought, price = futures.executed(futures.send_order(sym, "buy", size, "mkt", cli_ord_id="cfd-smoke-test"))
        if not bought:
            log("FAILED: the market buy was not filled")
            return False
        log(f"market buy {bought:g} {sym} @ {price:g}")
        stop = futures.send_order(sym, "sell", bought, "stp", stop_price=venue.round_price(spec, m * 0.95),
                                  reduce_only=True, trigger_signal=TRIGGER)["order_id"]
        ids.append(stop)
        tp = futures.send_order(sym, "sell", bought, "take_profit", stop_price=venue.round_price(spec, m * 1.05),
                                reduce_only=True, trigger_signal=TRIGGER)["order_id"]
        ids.append(tp)
        log("stop (-5 %) and take-profit (+5 %) rest on Kraken")
        futures.edit_order(stop, venue.round_price(spec, m * 0.96))
        resting = {o.get("order_id") for o in futures.open_orders()}
        if not {stop, tp} <= resting:
            log("FAILED: the stop / take-profit are not in the open orders")
            return False
        log("stop moved to -4 %, both orders listed")
        futures.cancel_order(tp)
        sold, exit_price = futures.executed(futures.send_order(sym, "sell", bought, "mkt", reduce_only=True))
        if sold < bought - 1e-12:
            log("FAILED: the reduce-only sell did not close the position")
            return False
        bought = 0.0
        log(f"reduce-only market sell @ {exit_price:g}")
        futures.cancel_order(stop)
        try:
            kinds = sorted({str(e.get("info")) for e in futures.account_log(count=50)})
            log(f"account log entry types seen: {', '.join(kinds) or '-'}")
        except Exception as e:
            log(f"account log not readable ({e}) - transfers into the wallet would count as profit")
        ok = True
        log("PASSED")
        return True
    except Exception as e:
        log(f"FAILED: {e}")
        return False
    finally:
        for order_id in ids:
            futures.cancel_order(order_id)
        if bought:
            try:
                futures.send_order(sym, "sell", bought, "mkt", reduce_only=True)
                log("clean-up: the test position was closed")
            except Exception as e:
                log(f"clean-up FAILED, close {bought:g} {sym} on Kraken by hand: {e}")
        futures.ALLOW = allow
        if not ok:
            log("the test did not pass: do not switch live on")
