"""How the experiment is doing, per book: closed trades by strategy, pair and side; the result of every day;
the regimes settled against the next day's move; holding the instruments as the benchmark."""
from datetime import datetime

from cfd import account
from cfd import report as cfd_report

from . import engine

FMT = "%Y-%m-%d %H:%M"


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def stats(rows):
    """Count, win rate, the result after costs, the result before costs (gross: net + spread + swap), average
    R and holding time, the costs and the exit reasons."""
    nets = [num(r["net_eur"]) or 0.0 for r in rows]
    spread = sum(num(r.get("spread_eur")) or 0.0 for r in rows)
    swap = sum(num(r.get("swap_eur")) or 0.0 for r in rows)
    rs = [num(r["r_multiple"]) for r in rows if num(r.get("r_multiple")) is not None]
    hours = [num(r.get("hours")) or 0.0 for r in rows]
    return {"n": len(rows),
            "win_rate_pct": round(sum(1 for n in nets if n > 0) / len(rows) * 100, 1) if rows else None,
            "net_eur": round(sum(nets), 2), "gross_eur": round(sum(nets) + spread + swap, 2),
            "avg_net_eur": round(sum(nets) / len(rows), 2) if rows else None,
            "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
            "avg_hours": round(sum(hours) / len(rows), 1) if rows else None,
            "spread_eur": round(spread, 2), "swap_eur": round(swap, 2),
            "by_reason": {k: sum(1 for r in rows if r["reason"] == k) for k in sorted({r["reason"] for r in rows})}}


def book_stats(rows):
    def by(key):
        return {k: stats([r for r in rows if r.get(key) == k]) for k in sorted({r.get(key) or "-" for r in rows})}
    return {"all": stats(rows), "by_strategy": by("strategy"), "by_pair": by("pair"), "by_side": by("side")}


def days(equity_by_day, start_eur, today=None):
    """The result of every day from the end-of-day equity: completed days only (today is still running)."""
    rows, prev = [], start_eur
    for day in sorted(equity_by_day):
        eq = equity_by_day[day]
        if day != today:
            rows.append({"date": day, "pnl_eur": round(eq - prev, 2), "pnl_pct": round((eq / prev - 1) * 100, 2) if prev else None})
        prev = eq
    green = sum(1 for r in rows if r["pnl_eur"] > 0)
    red = sum(1 for r in rows if r["pnl_eur"] < 0)
    pnls = [r["pnl_eur"] for r in rows]
    return {"n": len(rows), "green": green, "red": red, "flat": len(rows) - green - red,
            "green_pct": round(green / len(rows) * 100, 1) if rows else None,
            "avg_eur": round(sum(pnls) / len(pnls), 2) if pnls else None,
            "best_eur": max(pnls) if pnls else None, "worst_eur": min(pnls) if pnls else None,
            "today_eur": round(equity_by_day[today] - prev_of(equity_by_day, today, start_eur), 2)
            if today in equity_by_day else None,
            "last": rows[-30:]}


def prev_of(equity_by_day, day, start_eur):
    earlier = [d for d in equity_by_day if d < day]
    return equity_by_day[max(earlier)] if earlier else start_eur


def settle_regimes(rows):
    """Every regime against the move until the next regime of the same book and pair (about 24 hours; a gap
    over 36 hours is skipped). trend_up is right when the price rose, trend_down when it fell, range when the
    move stayed within one daily ATR."""
    groups = {}
    for r in rows:
        groups.setdefault((r["book"], r["pair"]), []).append(r)
    acc = {}
    for (book, _), rs in groups.items():
        rs.sort(key=lambda r: r["date"])
        for a, b in zip(rs, rs[1:]):
            p0, p1, atr = num(a["price"]), num(b["price"]), num(a.get("atr_pct"))
            gap = (datetime.strptime(b["date"], FMT) - datetime.strptime(a["date"], FMT)).total_seconds() / 3600
            if not p0 or not p1 or gap > 36:
                continue
            move = (p1 / p0 - 1) * 100
            s = acc.setdefault(book, {}).setdefault(a["regime"], {"n": 0, "moves": [], "in_atr": [], "hits": []})
            s["n"] += 1
            s["moves"].append(move)
            if atr:
                s["in_atr"].append(abs(move) / atr)
            hit = {"trend_up": move > 0, "trend_down": move < 0,
                   "range": abs(move) < atr if atr else None}.get(a["regime"])
            if hit is not None:
                s["hits"].append(hit)
    out = {}
    for book, regimes in acc.items():
        for name, s in regimes.items():
            out.setdefault(book, {})[name] = {
                "n": s["n"], "avg_move_pct": round(sum(s["moves"]) / len(s["moves"]), 2),
                "avg_abs_move_atr": round(sum(s["in_atr"]) / len(s["in_atr"]), 2) if s["in_atr"] else None,
                "hit_rate_pct": round(sum(s["hits"]) / len(s["hits"]) * 100, 1) if s["hits"] else None}
    return out


def evaluation(cfg, state, prices, regime_rows, trades, now):
    today = now.strftime("%Y-%m-%d")
    books = {}
    for name in engine.BOOKS:
        book = state["books"][name]
        eq = account.equity(cfg, book, prices)
        rows = [r for r in trades if r.get("book") == name]
        bench = cfd_report.benchmarks(cfg, {**book, "first_prices": state.get("first_prices"),
                                            "started": state.get("started")}, prices)
        books[name] = {"equity_eur": round(eq, 2), "return_pct": round((eq / book["start_eur"] - 1) * 100, 2),
                       "trades": book_stats(rows), "days": days(book.get("equity_by_day") or {}, book["start_eur"], today),
                       "benchmark": bench, "costs": book.get("costs")}
    return {"date": now.strftime(FMT), "books": books, "regimes": settle_regimes(regime_rows)}


def report_text(state):
    ev = state.get("evaluation")
    if not ev:
        return "No evaluation yet - it is computed after every refresh."
    lines = [f"Evaluation of {ev['date']}"]
    for name, b in ev["books"].items():
        bench = b.get("benchmark") or {}
        d = b["days"]
        lines.append(f"  {name} book: equity {b['equity_eur']} EUR ({b['return_pct']:+}%),"
                     f" vs holding {bench.get('vs_hold_pp', '-')} pp, days green {d['green']}/{d['n']}")
        for strategy, s in b["trades"]["by_strategy"].items():
            lines.append(f"    {strategy}: {s['n']} trades, win rate {s['win_rate_pct']}%, net {s['net_eur']} EUR"
                         f" (before costs {s['gross_eur']}), avg R {s['avg_r']}, avg {s['avg_hours']} h")
    for name, regimes in ev["regimes"].items():
        for regime_name, s in regimes.items():
            lines.append(f"  {name} regime {regime_name}: {s['n']} settled, avg move {s['avg_move_pct']:+}%,"
                         f" |move| {s['avg_abs_move_atr']} ATR, right {s['hit_rate_pct']}%")
    return "\n".join(lines)
