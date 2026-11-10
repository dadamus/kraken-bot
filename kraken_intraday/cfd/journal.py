"""calls.csv: one row per pair and cycle (the model's call and what the code did with it).
trades.csv: one row per closed position, with the costs."""
import csv

from . import settings

CALL_FIELDS = ["date", "pair", "price", "action", "confidence", "p_up_3d", "stop_price", "take_profit",
               "executed", "rule", "equity_eur", "reasoning"]
TRADE_FIELDS = ["opened", "closed", "pair", "side", "qty", "entry", "exit", "stop", "take_profit", "reason",
                "move_pct", "pnl_eur", "swap_eur", "spread_eur", "net_eur", "r_multiple", "days", "balance_eur"]


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


def log_calls(rows):
    append(settings.CALLS_FILE, CALL_FIELDS, rows)


def log_trades(rows):
    append(settings.TRADES_FILE, TRADE_FIELDS, rows)


def read_calls():
    return read(settings.CALLS_FILE)


def read_trades():
    return read(settings.TRADES_FILE)
