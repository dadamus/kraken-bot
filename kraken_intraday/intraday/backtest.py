"""The strategies replayed on past hourly candles: a first answer before the paper account has a month of its
own data.

The regime comes from the rule (regime.rules on the daily candles closed before each UTC day). The model's
regime cannot be replayed honestly - Claude already knows what happened after - so the backtest answers
"do the strategies with the rule earn more than their costs?", and the paper account answers "does the
model's regime do better than the rule?" (model book vs rules book).

Data: Kraken's public API returns the last 720 hourly candles (30 days). For a longer test, pass Kraken's
downloadable OHLCVT files (the 60-minute file per pair, columns time, open, high, low, close, volume, trades):
  python intraday_bot.py --backtest --csv XBTEUR=XBTEUR_60.csv --csv PAXGEUR=PAXGEUR_60.csv
Simplifications: the entry fills at the close of the candle that gave the signal (live: a few minutes later),
stops and take-profits fire on the hourly highs and lows (the stop first when both are inside one candle),
no blackout around releases (there is no calendar history), weekend hours as configured. The result is shown
after costs and before them (the same trades, spread and swap added back).
"""
import copy
import csv
import json
import math
import random
from datetime import datetime

from cfd import account, guard
from krakenbot import market

from . import engine, regime, report, settings

FMT = "%Y-%m-%d %H:%M"
WINDOW = 160  # hourly candles handed to the strategies (enough for the 50-hour EMA)
BOOK = "rules"


def read_csv(path):
    """Hourly candles from a Kraken OHLCVT file (a header line or a broken line is skipped)."""
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            try:
                t = int(float(row[0]))
                o, h, low, c = (float(x) for x in row[1:5])
            except (ValueError, IndexError):
                continue
            out.append({"t": t, "o": o, "h": h, "l": low, "c": c, "v": float(row[5]) if len(row) > 5 else 0.0})
    out.sort(key=lambda c: c["t"])
    steps = {b["t"] - a["t"] for a, b in zip(out[:50], out[1:51])}
    if out and min(steps or {3600}) != 3600:
        raise ValueError(f"{path}: not 60-minute candles (use the _60.csv file)")
    return out


def daily_from_hourly(hours):
    days = {}
    for c in hours:
        days.setdefault(c["t"] - c["t"] % 86400, []).append(c)
    return [{"t": t, "o": cs[0]["o"], "h": max(c["h"] for c in cs), "l": min(c["l"] for c in cs), "c": cs[-1]["c"],
             "v": sum(c["v"] for c in cs)} for t, cs in sorted(days.items())]


def fake_hours(price, n=24 * 120, seed=None):
    """--offline: a random walk with calm and trending stretches."""
    rnd = random.Random(seed)
    start = int(datetime.now().timestamp()) // 3600 * 3600 - n * 3600
    out, drift = [], 0.0
    for i in range(n):
        if i % 72 == 0:
            drift = rnd.choice((-0.0006, 0.0, 0.0, 0.0006))
        o = price
        price *= math.exp(rnd.gauss(drift, 0.005))
        out.append({"t": start + i * 3600, "o": o, "h": max(o, price) * (1 + abs(rnd.gauss(0, 0.002))),
                    "l": min(o, price) * (1 - abs(rnd.gauss(0, 0.002))), "c": price, "v": rnd.uniform(5, 15)})
    return out


def load(cfg, csv_args=(), offline=False):
    """({pair: hourly candles}, {pair: daily candles}, where the data came from)."""
    files = {}
    for arg in csv_args:
        pair, _, path = arg.partition("=")
        if not path:
            raise ValueError(f"--csv {arg}: use PAIR=FILE")
        files[pair.upper()] = path
    hourly, daily, source = {}, {}, {}
    for i, pair in enumerate(cfg["pairs"]):
        if offline:
            hours, api_days, source[pair] = fake_hours(100.0 * (i + 1), seed=i), [], "fake"
        elif pair in files:
            hours, source[pair] = read_csv(files[pair]), files[pair]
            api_days = market.optional(f"daily {pair}", lambda p=pair: market.fetch_ohlc(p, 720)[0]) or []
        else:
            hours, _ = market.fetch_ohlc(pair, 31, 60)
            api_days, _ = market.fetch_ohlc(pair, 720)
            source[pair] = "Kraken API (last 720 hours)"
        days = {c["t"]: c for c in daily_from_hourly(hours)}
        days.update({c["t"]: c for c in api_days})  # Kraken's own daily candles win; they also reach further back
        hourly[pair], daily[pair] = hours, [days[t] for t in sorted(days)]
    return hourly, daily, source


def simulate(cfg, hourly, daily):
    """Runs the rules book hour by hour. Returns (state, closed trades)."""
    cfg = copy.deepcopy(cfg)
    cfg["pairs"] = [p for p in cfg["pairs"] if hourly.get(p)]
    index = {pair: {c["t"]: i for i, c in enumerate(hs)} for pair, hs in hourly.items()}
    times = sorted({c["t"] for hs in hourly.values() for c in hs})
    state = engine.new_state(cfg, datetime.fromtimestamp(times[0]))
    book = state["books"][BOOK]
    trades, last, regime_day = [], {}, None
    for t in times:
        now = datetime.fromtimestamp(t + 3600)  # the candle has just closed
        bar = {pair: hourly[pair][index[pair][t]] for pair in cfg["pairs"] if t in index[pair]}
        for pair, c in bar.items():
            last[pair] = c["c"]
            state["first_prices"].setdefault(pair, c["o"])
        prices = dict(last)
        for pair in list(book["positions"]):  # stops and take-profits inside this candle
            pos, c = book["positions"][pair], bar.get(pair)
            if c and pos["opened_ts"] <= t and account.market_open(cfg, pair, datetime.fromtimestamp(t)):
                found = guard.hit(pos, c)
                if found:
                    trades.append(engine.close(cfg, book, BOOK, pair, found[1], found[0], now, prices))
        account.charge_swaps(cfg, book, prices, now)
        before = {pair: dict(pos) for pair, pos in book["positions"].items()}
        trades += [engine.annotate(r, BOOK, before[r["pair"]]) for r in account.stop_out(cfg, book, prices, now)]
        day = t - t % 86400
        if day != regime_day:  # the day's regime from the daily candles closed before it
            regime_day = day
            for pair in cfg["pairs"]:
                closed = [d for d in daily.get(pair, []) if d["t"] + 86400 <= day]
                state["regimes"][BOOK][pair] = {**regime.rules(cfg, closed), "confidence": None,
                                                "date": now.strftime(FMT)}
        candles = {pair: hourly[pair][max(0, index[pair][t] - WINDOW + 1):index[pair][t] + 1] for pair in bar}
        rows, _, _ = engine.step(cfg, state, BOOK, candles, prices, now)
        trades += rows
        engine.snapshot(cfg, state, prices, now)
    end = datetime.fromtimestamp(times[-1] + 3600)
    for pair in list(book["positions"]):
        trades.append(engine.close(cfg, book, BOOK, pair, last[pair], "END", end, last))
    engine.snapshot(cfg, state, last, end)
    return state, trades


def summary(cfg, state, trades, hourly, daily_regimes, source, now=None):
    now = now or datetime.now()
    book = state["books"][BOOK]
    first = min(hs[0]["t"] for hs in hourly.values() if hs)
    end = max(hs[-1]["t"] for hs in hourly.values() if hs) + 3600
    eq = book["balance"]  # everything is closed at the end
    holds = [hs[-1]["c"] / hs[0]["o"] if account.instrument(cfg, pair)["quote"] == "EUR" else 1.0
             for pair, hs in hourly.items() if hs]
    hold = sum(holds) / len(holds) if holds else 1.0
    return {"date": now.strftime(FMT), "from": datetime.fromtimestamp(first).strftime(FMT),
            "to": datetime.fromtimestamp(end).strftime(FMT), "hours": round((end - first) / 3600),
            "source": source, "start_eur": book["start_eur"], "equity_eur": round(eq, 2),
            "return_pct": round((eq / book["start_eur"] - 1) * 100, 2),
            "hold_return_pct": round((hold - 1) * 100, 2), "trades": report.book_stats(trades),
            "by_pair_strategy": {f"{p} {s}": report.stats([r for r in trades if r["pair"] == p and r["strategy"] == s])
                                 for p, s in sorted({(r["pair"], r["strategy"]) for r in trades})},
            "days": {k: v for k, v in report.days(book["equity_by_day"], book["start_eur"]).items() if k != "last"},
            "regime_days": daily_regimes}


def regime_days(cfg, daily, hourly):
    """How many days of the tested period each pair spent in each regime."""
    out = {}
    for pair, hs in hourly.items():
        if not hs:
            continue
        counts = {}
        for day in sorted({c["t"] - c["t"] % 86400 for c in hs}):
            name = regime.rules(cfg, [d for d in daily.get(pair, []) if d["t"] + 86400 <= day])["regime"]
            counts[name] = counts.get(name, 0) + 1
        out[pair] = counts
    return out


def text(result):
    a = result["trades"]["all"]
    d = result["days"]
    lines = [f"Backtest {result['from']} - {result['to']} ({result['hours']} h), rules regime, "
             f"start {result['start_eur']:g} EUR",
             f"  result {result['equity_eur']} EUR ({result['return_pct']:+}%), holding {result['hold_return_pct']:+}%",
             f"  {a['n']} trades, win rate {a['win_rate_pct']}%, net {a['net_eur']} EUR, before costs {a['gross_eur']}"
             f" EUR (spread {a['spread_eur']}, swap {a['swap_eur']}), avg R {a['avg_r']}",
             f"  days: {d['green']} green, {d['red']} red, {d['flat']} flat; avg {d['avg_eur']} EUR,"
             f" best {d['best_eur']}, worst {d['worst_eur']}"]
    for key, s in result["by_pair_strategy"].items():
        lines.append(f"  {key}: {s['n']} trades, win {s['win_rate_pct']}%, net {s['net_eur']} EUR"
                     f" (before costs {s['gross_eur']}), avg R {s['avg_r']}, avg {s['avg_hours']} h")
    for pair, counts in result["regime_days"].items():
        lines.append(f"  {pair} days per regime: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    lines.append(f"  data: {', '.join(f'{p}: {s}' for p, s in result['source'].items())}")
    return "\n".join(lines)


def run(cfg, csv_args=(), offline=False):
    hourly, daily, source = load(cfg, csv_args, offline)
    state, trades = simulate(cfg, hourly, daily)
    result = summary(cfg, state, trades, hourly, regime_days(cfg, daily, hourly), source)
    print(text(result))
    settings.BACKTEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    settings.BACKTEST_FILE.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


def load_result():
    try:
        return json.loads(settings.BACKTEST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
