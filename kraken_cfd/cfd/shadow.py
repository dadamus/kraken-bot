"""Shadow mode (execution: shadow): the paper account runs exactly as before, and every step it takes is turned
into the order Kraken Derivatives would need - contract, size rounded to the contract, the price it would get
from the order book now, stop and take-profit carried over to the contract's price, the margin at Kraken's
leverage - and logged to shadow.csv. Nothing is sent.

Each run also samples the contracts' spread and funding (state["kraken"]["quotes"]), with a derivatives key
("Read Only" is enough) checks the wallet, and the daily cycle re-prices every closed paper trade with Kraken's
costs (taker fee on both sides, the measured spread, the real hourly funding over the holding time). That
re-pricing is the number to look at before going live: the paper account pays a CFD broker's costs.

Nothing here may stop the paper account: every error is printed and kept in state["kraken"]["error"].
"""
import csv
from datetime import datetime

from krakenbot import alerts

from . import account, futures, settings, venue

FMT = "%Y-%m-%d %H:%M"
FIELDS = ["date", "kind", "pair", "symbol", "side", "paper_qty", "size", "paper_price", "contract_price",
          "slippage_bps", "stop", "take_profit", "margin_eur", "max_leverage", "ok", "why"]
ACCOUNT_CHECK_S = 3600


def snapshot(state):
    return {pair: dict(pos) for pair, pos in state["positions"].items()}


def steps(before, after, closed):
    """What the paper account did between two snapshots: the closes (trades.csv rows), then the opens and the
    stop moves."""
    out = [{"kind": "close", "pair": tr["pair"], "side": tr["side"], "qty": float(tr["qty"]),
            "price": float(tr["exit"]), "reason": tr["reason"]} for tr in closed]
    for pair, pos in after.items():
        old = before.get(pair)
        if not old or old.get("opened_ts") != pos.get("opened_ts"):
            out.append({"kind": "open", "pair": pair, "side": pos["side"], "qty": pos["qty"], "price": pos["entry"],
                        "stop": pos["stop"], "take_profit": pos["take_profit"]})
        elif old.get("stop") != pos.get("stop"):
            moved = "break-even" if pos.get("breakeven") and not old.get("breakeven") else "model"
            out.append({"kind": "stop_move", "pair": pair, "side": pos["side"], "qty": pos["qty"],
                        "price": None, "stop": pos["stop"], "take_profit": pos["take_profit"], "reason": moved})
    return out


def order_row(cfg, state, step, specs, prices, now, ticker):
    pair, kind = step["pair"], step["kind"]
    sym = venue.symbol(cfg, pair)
    spec = specs.get(sym)
    buying = (step["side"] == "long") == (kind == "open")  # a stop order sells a long
    row = {"date": now.strftime(FMT), "kind": kind if kind != "close" else f"close ({step['reason']})",
           "pair": pair, "symbol": sym, "side": "buy" if buying else "sell", "paper_qty": f"{step['qty']:.8g}",
           "paper_price": f"{step['price']:.8g}" if step.get("price") else ""}
    if not venue.available(spec):
        return {**row, "ok": False, "why": venue.why_unavailable(spec, sym)}
    tick = ticker(sym)
    spot = prices.get(pair) or step.get("price")
    contract_mid = venue.mid(tick)
    size = venue.round_size(spec, step["qty"])
    why = []
    if size < venue.min_size(spec):
        why.append(f"size {step['qty']:.6g} below the contract's minimum {venue.min_size(spec):g}")
    if spec["max_position"] and size > spec["max_position"]:
        why.append(f"size above Kraken's maximum position {spec['max_position']:g}")
    row.update(size=f"{size:g}", max_leverage=spec["max_leverage"])
    if kind in ("open", "stop_move"):
        row["stop"] = venue.round_price(spec, venue.carry(step["stop"], spot, contract_mid))
        row["take_profit"] = venue.round_price(spec, venue.carry(step["take_profit"], spot, contract_mid))
    resting = step.get("reason") in ("STOP", "TAKE-PROFIT")  # orders waiting on Kraken, not market orders
    if kind != "stop_move" and not resting:
        # a market order: what the book gives now, against the paper fill (both in the spot pair's terms)
        price = float(tick["ask"] if buying else tick["bid"])
        row["contract_price"] = f"{price:.8g}"
        in_spot = spot * price / contract_mid
        paper = float(step["price"])
        row["slippage_bps"] = round(((in_spot - paper) if buying else (paper - in_spot)) / paper * 1e4, 1)
    elif kind == "close":  # a resting stop / take-profit order filled: Kraken triggers it on the mark price
        row["contract_price"] = f"{venue.round_price(spec, venue.carry(step['price'], spot, contract_mid)):.8g}"
    if kind == "open" and spec["max_leverage"]:
        margin = account.notional_eur(cfg, pair, size, spot, prices) / spec["max_leverage"]
        row["margin_eur"] = round(margin, 2)
        free = ((state.get("kraken") or {}).get("account") or {}).get("available_margin_eur")
        if free is not None and margin > free:
            why.append(f"margin {margin:.2f} EUR above the wallet's free margin {free:.2f} EUR")
        lev = account.leverage(cfg, pair)
        if lev > spec["max_leverage"]:
            why.append(f"note: paper leverage {lev:g} > Kraken's {spec['max_leverage']:g} (more margin)")
    row["ok"] = not [w for w in why if not w.startswith("note:")]
    row["why"] = "; ".join(why)
    return row


def log(rows):
    if not rows:
        return
    path = settings.SHADOW_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def read():
    try:
        with settings.SHADOW_FILE.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def failed(state, what, e):
    msg = f"{what}: {e}"
    print(f"  [kraken shadow] {msg}")
    state.setdefault("kraken", {})["error"] = {"at": datetime.now().strftime(FMT), "message": msg[:300]}


def record(cfg, state, before, closed, prices, now, ticker=None, fetch_specs=None):
    """The shadow orders for what the paper account did since `before`. Returns the shadow.csv rows."""
    ticker = ticker or futures.ticker
    try:
        acts = steps(before, state["positions"], closed)
        if not acts:
            return []
        specs = venue.specs(state, venue.all_symbols(cfg), now, fetch_specs)
        cache = {}
        rows = [order_row(cfg, state, a, specs, prices, now, lambda s: cache.get(s) or cache.setdefault(s, ticker(s)))
                for a in acts]
        log(rows)
        for r in rows:
            print(f"  [kraken shadow] {r['kind']} {r['pair']} -> {r['side']} {r.get('size', '-')} {r['symbol']}"
                  f"{' @ ' + r['contract_price'] if r.get('contract_price') else ''}: {'ok' if r['ok'] else 'NOT OK'}"
                  f"{' (' + r['why'] + ')' if r['why'] else ''}")
        return rows
    except Exception as e:
        failed(state, "shadow orders", e)
        return []


def sample(cfg, state, now, ticker=None, fetch_specs=None):
    """Spread and funding of every candidate contract, as running sums (averages in quote_stats)."""
    ticker = ticker or futures.ticker
    try:
        specs = venue.specs(state, venue.all_symbols(cfg), now, fetch_specs)
    except Exception as e:
        failed(state, "contract specs", e)
        return
    quotes = state.setdefault("kraken", {}).setdefault("quotes", {})
    for sym in venue.all_symbols(cfg):
        if not venue.available(specs.get(sym)):
            continue
        try:
            tick = ticker(sym)
        except Exception as e:
            failed(state, f"ticker {sym}", e)
            continue
        spread, funding = venue.spread_pct(tick), venue.funding_pct_day(tick)
        q = quotes.setdefault(sym, {"since": now.strftime(FMT), "n": 0, "spread_sum": 0.0, "spread_max": 0.0,
                                    "funding_n": 0, "funding_sum": 0.0})
        if spread is not None:
            q["n"] += 1
            q["spread_sum"] += spread
            q["spread_max"] = max(q["spread_max"], spread)
            q["last_spread_pct"] = round(spread, 4)
        if funding is not None:
            q["funding_n"] += 1
            q["funding_sum"] += funding
            q["last_funding_pct_day"] = round(funding, 4)
        q["last"] = now.strftime(FMT)


def quote_stats(cfg, state):
    """Per traded and measured contract: the average spread and funding seen, next to the paper's costs."""
    quotes = (state.get("kraken") or {}).get("quotes") or {}
    out = {}
    for pair in cfg["pairs"]:
        inst = account.instrument(cfg, pair)
        for sym in venue.symbols(cfg, pair):
            q = quotes.get(sym)
            if not q:
                continue
            out[sym] = {"pair": pair, "traded": sym == venue.symbol(cfg, pair), "since": q["since"],
                        "samples": q["n"],
                        "avg_spread_pct": round(q["spread_sum"] / q["n"], 4) if q["n"] else None,
                        "max_spread_pct": round(q["spread_max"], 4),
                        "paper_spread_pct": inst["spread_pct"],
                        "avg_funding_long_pct_day": round(q["funding_sum"] / q["funding_n"], 4)
                        if q["funding_n"] else None,
                        "paper_swap_pct_day": inst["swap_pct_per_day"]}
    return out


def check_account(cfg, state, prices, now, force=False):
    """With a derivatives key: the wallet's value and free margin (and a proof that the key and the signing
    work). At most once an hour unless forced. Problems go to the phone through the alerts."""
    if not futures.has_keys():
        return None
    k = state.setdefault("kraken", {})
    last = (k.get("account") or {}).get("checked_ts", 0)
    if not force and now.timestamp() - last < ACCOUNT_CHECK_S:
        return k.get("account")
    try:
        flex = futures.flex_account()
        positions = futures.open_positions()
        usd_eur = 1 / prices["EURUSD"] if prices.get("EURUSD") else None
        value = float(flex.get("portfolioValue") or 0)
        free = float(flex.get("availableMargin") or 0)
        k["account"] = {"checked": now.strftime(FMT), "checked_ts": now.timestamp(),
                        "portfolio_eur": round(value * usd_eur, 2) if usd_eur else None,
                        "available_margin_eur": round(free * usd_eur, 2) if usd_eur else None,
                        "portfolio_usd": round(value, 2), "open_positions": len(positions)}
        alerts.problem(state, "kraken_futures_key")
        print(f"  [kraken] derivatives wallet {value:,.2f} USD, free margin {free:,.2f} USD,"
              f" {len(positions)} open position(s)")
    except Exception as e:
        failed(state, "derivatives account", e)
        alerts.problem(state, "kraken_futures_key", f"CFD: the Kraken derivatives key does not work: {e}")
    return k.get("account")


def ts(text):
    return datetime.strptime(text, FMT).timestamp()


def reprice(cfg, state, trades, now, funding=None, ticker=None, fetch_specs=None, keep=30):
    """Every closed paper trade with Kraken's costs instead of the paper's: the move between the paper's mid
    prices, minus the taker fee on both sides, the measured spread (else the current one) and the funding
    actually paid over the holding time. Trades of a contract EEA clients cannot trade are counted apart."""
    funding, ticker = funding or futures.funding_history, ticker or futures.ticker
    try:
        specs = venue.specs(state, venue.all_symbols(cfg), now, fetch_specs)
    except Exception as e:
        failed(state, "contract specs", e)
        return None
    stats = quote_stats(cfg, state)
    history, spreads, rows = {}, {}, []
    for tr in trades:
        pair, sym = tr["pair"], venue.symbol(cfg, tr["pair"])
        base = {"pair": pair, "symbol": sym, "side": tr["side"], "opened": tr["opened"], "closed": tr["closed"],
                "reason": tr["reason"], "paper_net_eur": float(tr["net_eur"])}
        if not venue.available(specs.get(sym)):
            rows.append({**base, "status": "unavailable"})
            continue
        try:
            if sym not in history:
                history[sym] = funding(sym)
            if sym not in spreads:
                s = (stats.get(sym) or {}).get("avg_spread_pct")
                spreads[sym] = s if s is not None else venue.spread_pct(ticker(sym))
        except Exception as e:
            failed(state, f"funding / spread {sym}", e)
            return None
        qty, entry, exit_ = float(tr["qty"]), float(tr["entry"]), float(tr["exit"])
        fx = 1.0 if account.instrument(cfg, pair)["quote"] == "EUR" else None
        if fx is None:  # a USD pair: the notional in EUR from the paper's own spread cost
            pct = account.spread_pct(cfg, pair)
            open_n = float(tr["spread_eur"]) * 100 / pct / 2 if pct else 0.0
            close_n = open_n
        else:
            open_n, close_n = qty * entry, qty * exit_
        t0, t1 = ts(tr["opened"]), ts(tr["closed"])
        hist = history[sym]
        if not hist or hist[0][0] > t0:
            rows.append({**base, "status": "no funding history"})
            continue
        rate = sum(r for t, r in hist if t0 <= t < t1)
        sign = 1 if tr["side"] == "long" else -1
        fee = (open_n + close_n) * venue.TAKER_FEE_PCT / 100
        spread = (open_n + close_n) / 2 * (spreads[sym] or 0) / 100
        fund = open_n * rate * sign
        move = float(tr["pnl_eur"]) + float(tr["spread_eur"])  # the paper's P&L between mid prices
        rows.append({**base, "status": "ok", "fee_eur": round(fee, 2), "spread_eur": round(spread, 2),
                     "funding_eur": round(fund, 2), "kraken_net_eur": round(move - fee - spread - fund, 2)})
    ok = [r for r in rows if r["status"] == "ok"]
    other = [r for r in rows if r["status"] != "ok"]
    total = {"n": len(ok), "paper_net_eur": round(sum(r["paper_net_eur"] for r in ok), 2),
             "kraken_net_eur": round(sum(r["kraken_net_eur"] for r in ok), 2),
             "fee_eur": round(sum(r["fee_eur"] for r in ok), 2),
             "spread_eur": round(sum(r["spread_eur"] for r in ok), 2),
             "funding_eur": round(sum(r["funding_eur"] for r in ok), 2),
             "not_tradable_n": len(other), "not_tradable_paper_net_eur": round(sum(r["paper_net_eur"] for r in other), 2),
             "not_tradable_pairs": sorted({r["pair"] for r in other})}
    return {"date": now.strftime(FMT), "total": total, "trades": rows[-keep:]}
