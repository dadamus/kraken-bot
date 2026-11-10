"""regimes.csv: one row per book, pair and daily cycle (the regime and the price it was set at).
trades.csv: one row per closed position, with the book, the strategy and the costs."""
import csv

from . import settings

REGIME_FIELDS = ["date", "book", "pair", "price", "regime", "confidence", "atr_pct", "reasoning"]
TRADE_FIELDS = ["opened", "closed", "book", "pair", "strategy", "regime", "side", "qty", "entry", "exit", "stop",
                "take_profit", "reason", "move_pct", "pnl_eur", "swap_eur", "spread_eur", "net_eur", "r_multiple",
                "hours", "balance_eur"]


def append(path, fields, rows):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def read(path):
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def log_regimes(rows):
    append(settings.REGIMES_FILE, REGIME_FIELDS, rows)


def log_trades(rows):
    append(settings.TRADES_FILE, TRADE_FIELDS, rows)


def read_regimes():
    return read(settings.REGIMES_FILE)


def read_trades():
    return read(settings.TRADES_FILE)
