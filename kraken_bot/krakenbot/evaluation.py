"""Performance: settling the model's calls, benchmark strategies and the --report summary."""
import time
from datetime import datetime

from . import journal, market, portfolio
from .i18n import t


HORIZONS = (1, 3, 7)  # after how many days the model's calls are settled
P_UP_BUCKETS = ((0, .35), (.35, .45), (.45, .55), (.55, .65), (.65, 1.01))
MIN_SETTLED = 30  # below this the results are mostly noise


def row_ts(r):
    return datetime.strptime(r["date"], "%Y-%m-%d %H:%M").timestamp()


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def rnd(x, n=2):
    return round(x, n) if x is not None else None


def price_after(series, ts, days, tol_h=8):
    """Price approx. N days after ts: the nearest point of series [(ts, price)] if within ±tol_h."""
    target = ts + days * 86400
    best = min(series, key=lambda p: abs(p[0] - target))
    return best[1] if abs(best[0] - target) <= tol_h * 3600 else None


def price_series(rows, candles=None, prices=None):
    """Known prices per pair [(ts, price)]: log rows, Kraken daily closes and the current price."""
    series = {}
    for r in rows:
        series.setdefault(r["pair"], []).append((row_ts(r), float(r["price"])))
    for pair, cs in (candles or {}).items():
        series.setdefault(pair, []).extend((c["t"] + 86400, c["c"]) for c in cs)
    for pair, p in (prices or {}).items():
        series.setdefault(pair, []).append((time.time(), p))
    return series


def returns_after(series, r, tol_h=8):
    ts, p0 = row_ts(r), float(r["price"])
    rets = {}
    for n in HORIZONS:
        p = price_after(series[r["pair"]], ts, n, tol_h)
        rets[n] = (p / p0 - 1) * 100 if p else None
    return rets


def cycle_kind(r):
    """"daily" or "extra" (rows from before the cycle column count as daily)."""
    return "extra" if (r.get("cycle") or "").startswith("extra") else "daily"


def calibration(probs):
    brier = mean((p - up) ** 2 for p, up in probs)
    return {"settled": len(probs), "brier": rnd(brier, 4),
            "brier_skill_pct": rnd((1 - brier / 0.25) * 100, 1) if brier is not None else None,
            "hit_rate_pct": rnd(mean(100.0 * ((p > 0.5) == up) for p, up in probs if p != 0.5), 1)}


def stop_audit(rows, series, recent=5):
    """Automatic stop / take-profit sales: what the price did 1/3/7 days after the sale. Lower afterwards
    = the stop saved money; higher = it sold too early (buying back costs fees on top). Prices come from
    daily closes, so they are accurate to about half a day."""
    stops = [r for r in rows if r["model_action"] == "stop" and r["executed"] == "sell"]
    if not stops:
        return None
    done = [(r, returns_after(series, r, tol_h=12)) for r in stops]
    out = {"stops": len(stops)}
    for n in HORIZONS:
        out[f"avg_{n}d_pct"] = rnd(mean(x[n] for _, x in done if x[n] is not None))
    r3 = [x[3] for _, x in done if x[3] is not None]
    out["settled_3d"] = len(r3)
    out["lower_after_3d"] = sum(1 for x in r3 if x < 0)
    out["recent"] = [{"date": r["date"], "pair": r["pair"], "rule": r["rule"], "price": float(r["price"]),
                      **{f"after_{n}d_pct": rnd(x[n]) for n in HORIZONS}} for r, x in done[-recent:]]
    return out


def evaluate_signals(rows, candles=None, prices=None):
    """Settles the model's calls from the log: what the price did 1/3/7 days later. The later price comes
    from later log rows or, when missing, from the close of a Kraken daily candle (accurate to a few
    hours). Also settles the automatic stop sales and splits the calibration by daily / extra cycles."""
    series = price_series(rows, candles, prices)
    sigs = [{"action": r["model_action"], "rets": returns_after(series, r), "cycle": cycle_kind(r),
             "traded": r["executed"] in ("buy", "sell"),
             "p_up": float(r["p_up_3d"]) if r.get("p_up_3d") else None}
            for r in rows if r["model_action"] != "stop"]

    by_action = {}
    for a in ("buy", "hold", "sell"):
        s = [x for x in sigs if x["action"] == a]
        if not s:
            continue
        st = {"signals": len(s)}
        for n in HORIZONS:
            st[f"avg_{n}d_pct"] = rnd(mean(x["rets"][n] for x in s if x["rets"][n] is not None))
        r3 = [x["rets"][3] for x in s if x["rets"][3] is not None]
        st["settled_3d"] = len(r3)
        st["up_3d_pct"] = rnd(mean(100.0 * (x > 0) for x in r3), 1)
        by_action[a] = st

    def probs_of(xs):
        return [(x["p_up"], x["rets"][3] > 0) for x in xs if x["p_up"] is not None and x["rets"][3] is not None]

    probs = probs_of(sigs)
    by_cycle = {}
    for kind in ("daily", "extra"):
        s = [x for x in sigs if x["cycle"] == kind]
        if s:
            by_cycle[kind] = {"calls": len(s), "trades": sum(1 for x in s if x["traded"]),
                              **calibration(probs_of(s))}
    buckets = []
    for lo, hi in P_UP_BUCKETS:
        b = [(p, up) for p, up in probs if lo <= p < hi]
        if b:
            buckets.append({"p_range": f"{lo:.2f}-{min(hi, 1):.2f}", "n": len(b),
                            "avg_p": rnd(mean(p for p, _ in b)),
                            "actual_up_pct": rnd(mean(100.0 * up for _, up in b), 1)})
    return {"by_action": by_action, "p_up_3d": {**calibration(probs), "buckets": buckets},
            "by_cycle": by_cycle, "stops": stop_audit(rows, series)}


def shadow_strategies(cfg, rows, candles, prices, sma_days=50):
    """Simple benchmark strategies since the first decision in the log, with fees and slippage:
    buy and hold, and trend (in the market while the daily close is above SMA50, decided at the close)."""
    cost = (cfg["fee_pct"] + cfg.get("slippage_pct", 0.1)) / 100
    per_pair = {}
    for pair, cs in candles.items():
        first = next((r for r in rows if r["pair"] == pair), None)
        if not first or pair not in prices or len(cs) < sma_days:
            continue
        t0, p0 = row_ts(first), float(first["price"])
        closes = [c["c"] for c in cs] + [prices[pair]]
        times = [c["t"] + 86400 for c in cs] + [time.time()]
        start = next((i for i, ts in enumerate(times) if ts > t0), len(closes) - 1)

        def above_sma(i):
            return i >= sma_days - 1 and closes[i] > sum(closes[i - sma_days + 1:i + 1]) / sma_days

        v, last, inside = 1.0, p0, start > 0 and above_sma(start - 1)
        if inside:
            v *= 1 - cost
        for i in range(start, len(closes)):
            if inside:
                v *= closes[i] / last
            last = closes[i]
            if i < len(closes) - 1 and above_sma(i) != inside:
                v *= 1 - cost
                inside = not inside
        per_pair[pair] = {"buy_and_hold_pct": rnd((prices[pair] / p0 * (1 - cost) - 1) * 100),
                          "trend_pct": rnd((v - 1) * 100), "trend_in_market": inside}
    if not per_pair:
        return None
    return {"since": rows[0]["date"][:10], "trend_rule": t("trend_rule", days=sma_days),
            "buy_and_hold_pct": rnd(mean(p["buy_and_hold_pct"] for p in per_pair.values())),
            "trend_pct": rnd(mean(p["trend_pct"] for p in per_pair.values())),
            "per_pair": per_pair}


def evaluation(cfg, state, rows, candles, prices):
    runs = {r["date"]: (float(r["eur"]), float(r["equity_eur"])) for r in rows
            if r["model_action"] != "stop"}  # model cycles; stop sales between them are not cycles
    ev = {"updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
          "bot": {"return_pct": rnd((portfolio.equity_of(state, prices) / state["start_eur"] - 1) * 100),
                  "avg_exposure_pct": rnd(mean((1 - e / q) * 100 for e, q in runs.values() if q), 1),
                  "cycles": len(runs)},
          **evaluate_signals(rows, candles, prices)}
    if candles and rows:
        ev["shadow"] = shadow_strategies(cfg, rows, candles, prices)
    return ev


def evaluation_lines(ev):
    def f(x, unit="%"):
        return "-" if x is None else f"{x:+.2f}{unit}"

    b, s, c = ev["bot"], ev.get("shadow"), ev["p_up_3d"]
    out = [t("ev_bot", ret=f(b["return_pct"]), exposure=b["avg_exposure_pct"], cycles=b["cycles"])]
    if s:
        out.append(t("ev_shadow", since=s["since"], bh=f(s["buy_and_hold_pct"]), rule=s["trend_rule"],
                     trend=f(s["trend_pct"])))
    out.append(t("ev_header"))
    for a, st in ev["by_action"].items():
        up = "-" if st["up_3d_pct"] is None else f"{st['up_3d_pct']:.0f}%"
        out.append(t("ev_action", action=a, n=st["signals"], up=up, settled=st["settled_3d"],
                     rets=" / ".join(f(st[f"avg_{n}d_pct"]) for n in HORIZONS)))
    if c["settled"]:
        hit = "-" if c["hit_rate_pct"] is None else f"{c['hit_rate_pct']:.0f}%"
        out.append(t("ev_calibration", n=c["settled"], hit=hit, brier=c["brier"], skill=f(c["brier_skill_pct"])))
        for bk in c["buckets"]:
            out.append(t("ev_bucket", range=bk["p_range"], n=bk["n"], avg=bk["avg_p"], up=bk["actual_up_pct"]))
    else:
        out.append(t("ev_nothing"))
    for kind, st in (ev.get("by_cycle") or {}).items():
        hit = "-" if st["hit_rate_pct"] is None else f"{st['hit_rate_pct']:.0f}%"
        out.append(t("ev_cycle", kind=kind, calls=st["calls"], trades=st["trades"], n=st["settled"], hit=hit,
                     brier="-" if st["brier"] is None else st["brier"]))
    st = ev.get("stops")
    if st:
        out.append(t("ev_stops", n=st["stops"], rets=" / ".join(f(st[f"avg_{n}d_pct"]) for n in HORIZONS),
                     lower=st["lower_after_3d"], settled=st["settled_3d"]))
    if c["settled"] < MIN_SETTLED:
        out.append(t("ev_noise", n=MIN_SETTLED))
    return out


def api_cost_lines(state):
    u = state.get("api_usage")
    if not u:
        return []
    return [t("ev_api_cost", month=u["month"], cost=u["cost_usd"], calls=u["calls"], total=u["total_cost_usd"],
              total_calls=u["total_calls"])]


def report(cfg, state):
    rows = journal.read_log()
    if not rows:
        print(t("no_history"))
        return
    days = {r["date"][:10] for r in rows}
    eq0, eq1 = float(rows[0]["equity_eur"]), float(rows[-1]["equity_eur"])
    trades = sum(1 for r in rows if r["executed"] in ("buy", "sell"))
    print(t("report_days", days=len(days), trades=trades))
    print(t("report_bot", eq0=eq0, eq1=eq1, pct=(eq1 / eq0 - 1) * 100))
    for pair in sorted({r["pair"] for r in rows}):
        prs = [r for r in rows if r["pair"] == pair]
        bought = sum(1 for r in prs if r["executed"] == "buy")
        sold = sum(1 for r in prs if r["executed"] == "sell")
        print(t("report_pair", pair=pair, bought=bought, sold=sold))

    candles, prices = {}, {}
    for pair in cfg["pairs"]:
        got = market.optional(f"kraken {pair}", market.fetch_ohlc, pair, 720)
        if got:
            candles[pair], prices[pair] = got
        else:  # offline: the last price from the log
            last = [r for r in rows if r["pair"] == pair]
            if last:
                prices[pair] = float(last[-1]["price"])
    print()
    for line in evaluation_lines(evaluation(cfg, state, rows, candles, prices)) + api_cost_lines(state):
        print(line)
