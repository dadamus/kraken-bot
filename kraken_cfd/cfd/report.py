"""How the experiment is doing: closed trades by side, the model's calls settled after 3 days, and the
benchmark of simply holding the coins over the same period."""
from datetime import datetime

from . import account

FMT = "%Y-%m-%d %H:%M"
HORIZON = 3 * 86400  # p_up_3d and the calls are settled 72 h after the call


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def trade_stats(trades):
    """Per side and in total: count, win rate, net result, average R and the costs paid."""
    def stats(rows):
        nets = [num(r["net_eur"]) or 0.0 for r in rows]
        rs = [num(r["r_multiple"]) for r in rows if num(r["r_multiple"]) is not None]
        return {"n": len(rows),
                "win_rate_pct": round(sum(1 for n in nets if n > 0) / len(rows) * 100, 1) if rows else None,
                "net_eur": round(sum(nets), 2),
                "avg_net_eur": round(sum(nets) / len(rows), 2) if rows else None,
                "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
                "avg_days": round(sum(num(r["days"]) or 0 for r in rows) / len(rows), 2) if rows else None,
                "swap_eur": round(sum(num(r["swap_eur"]) or 0 for r in rows), 2),
                "spread_eur": round(sum(num(r["spread_eur"]) or 0 for r in rows), 2),
                "by_reason": {k: sum(1 for r in rows if r["reason"] == k) for k in sorted({r["reason"] for r in rows})}}
    return {"all": stats(trades), "long": stats([r for r in trades if r["side"] == "long"]),
            "short": stats([r for r in trades if r["side"] == "short"])}


def close_after(candles, ts):
    """The close of the daily candle that contains `ts` (None while that day is not closed yet)."""
    for c in candles:
        if c["t"] <= ts < c["t"] + 86400:
            return c["c"]
    return None


def settle_calls(calls, closed_by_pair):
    """Every call 3 days later: the move in the call's direction and the p_up_3d forecast."""
    by_action, forecasts = {}, []
    for r in calls:
        candles, price = closed_by_pair.get(r["pair"]), num(r["price"])
        if not candles or not price:
            continue
        ts = datetime.strptime(r["date"], FMT).timestamp()
        later = close_after(candles, ts + HORIZON)
        if later is None:
            continue
        move = (later / price - 1) * 100
        a = by_action.setdefault(r["action"], {"n": 0, "moves": []})
        a["n"] += 1
        a["moves"].append(move)
        p = num(r["p_up_3d"])
        if p is not None:
            forecasts.append((p, 1.0 if move > 0 else 0.0))
    out = {}
    for action, a in by_action.items():
        moves = a["moves"]
        sign = {"long": 1, "short": -1}.get(action)
        row = {"n": a["n"], "avg_move_pct": round(sum(moves) / len(moves), 2)}
        if sign:  # the move in the call's direction and how often it went that way
            row["avg_move_in_direction_pct"] = round(sign * sum(moves) / len(moves), 2)
            row["hit_rate_pct"] = round(sum(1 for m in moves if m * sign > 0) / len(moves) * 100, 1)
        out[action] = row
    brier = round(sum((p - o) ** 2 for p, o in forecasts) / len(forecasts), 4) if forecasts else None
    hits = sum(1 for p, o in forecasts if p != 0.5 and (p > 0.5) == (o == 1.0))
    decided = sum(1 for p, _ in forecasts if p != 0.5)
    # the base rate: always forecasting the share of rises actually seen (known only afterwards). In a strong
    # trend it scores far below the coin flip's 0.25, and a hit rate far above 50 % comes almost by itself
    rate = sum(o for _, o in forecasts) / len(forecasts) if forecasts else None
    return {"by_action": out, "p_up_3d": {"n": len(forecasts), "brier": brier,
                                          "hit_rate_pct": round(hits / decided * 100, 1) if decided else None,
                                          "up_rate_pct": round(rate * 100, 1) if rate is not None else None,
                                          "brier_base_rate": round(rate * (1 - rate), 4) if rate is not None else None}}


def benchmarks(cfg, state, prices):
    """Holding the pairs in equal parts since the start, unleveraged and without costs (a generous benchmark);
    a currency pair counts as EUR cash, an instrument priced in USD (oil) by its price (the dollar's move
    against the euro is left out)."""
    first = {p: v for p, v in (state.get("first_prices") or {}).items() if p in prices and v}
    if not first:
        return None
    # holding a currency pair from a EUR account means holding EUR: no change
    growth = sum(1.0 if account.instrument(cfg, p)["asset_class"] == "currency" else prices[p] / first[p]
                 for p in first) / len(first)
    eq = account.equity(cfg, state, prices)
    hold_eur = state["start_eur"] * growth
    return {"since": state.get("started"), "hold_return_pct": round((growth - 1) * 100, 2),
            "hold_eur": round(hold_eur, 2), "vs_hold_eur": round(eq - hold_eur, 2),
            "vs_hold_pp": round((eq / state["start_eur"] - growth) * 100, 2)}


def evaluation(cfg, state, prices, closed_by_pair, calls, trades, now):
    eq = account.equity(cfg, state, prices)
    return {"date": now.strftime(FMT), "equity_eur": round(eq, 2),
            "return_pct": round((eq / state["start_eur"] - 1) * 100, 2),
            "trades": trade_stats(trades), "calls": settle_calls(calls, closed_by_pair),
            "benchmark": benchmarks(cfg, state, prices)}


def report_text(state):
    ev = state.get("evaluation")
    if not ev:
        return "No evaluation yet - it is computed at the end of every daily cycle."
    lines = [f"Evaluation of {ev['date']}: equity {ev['equity_eur']} EUR ({ev['return_pct']:+}%)"]
    b = ev.get("benchmark")
    if b:
        lines.append(f"  holding the pairs since {b['since']}: {b['hold_return_pct']:+}%,"
                     f" the simulator {b['vs_hold_pp']:+} pp ({b['vs_hold_eur']:+} EUR)")
    for side in ("long", "short", "all"):
        s = ev["trades"][side]
        lines.append(f"  trades {side}: {s['n']}, win rate {s['win_rate_pct']}%, net {s['net_eur']} EUR,"
                     f" avg R {s['avg_r']}, swap {s['swap_eur']}, spread {s['spread_eur']}")
    for action, a in ev["calls"]["by_action"].items():
        extra = (f", in direction {a['avg_move_in_direction_pct']:+}%, hit {a['hit_rate_pct']}%"
                 if "hit_rate_pct" in a else "")
        lines.append(f"  calls {action}: {a['n']}, avg 3-day move {a['avg_move_pct']:+}%{extra}")
    p = ev["calls"]["p_up_3d"]
    base = (f"; the price rose in {p['up_rate_pct']}% of them, always forecasting that scores Brier"
            f" {p['brier_base_rate']}" if p.get("brier_base_rate") else "")  # 0: every call went one way
    lines.append(f"  p_up_3d: {p['n']} settled, Brier {p['brier']}, hit rate {p['hit_rate_pct']}%{base}")
    k = ev.get("kraken")
    if k:  # shadow mode: the same trades with Kraken Derivatives' costs instead of the paper's
        tot = k["total"]
        lines.append(f"  with Kraken's costs ({tot['n']} trades): paper net {tot['paper_net_eur']} EUR ->"
                     f" {tot['kraken_net_eur']} EUR (fees {tot['fee_eur']}, spread {tot['spread_eur']},"
                     f" funding {tot['funding_eur']})")
        if tot["not_tradable_n"]:
            lines.append(f"  not tradable on Kraken in the EEA: {tot['not_tradable_n']} trades on"
                         f" {', '.join(tot['not_tradable_pairs'])} (paper net {tot['not_tradable_paper_net_eur']} EUR)")
    return "\n".join(lines)
