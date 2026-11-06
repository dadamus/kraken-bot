"""The decision log (decisions.csv / decisions_live.csv)."""
import csv
from datetime import datetime

from . import config

LOG_FIELDS = ["date", "pair", "price", "model_action", "confidence", "p_up_3d", "size_pct", "executed",
              "rule", "qty", "eur", "coin", "equity_eur", "reasoning", "cycle"]


def read_log():
    if not config.LOG_FILE.exists():
        return []
    with config.LOG_FILE.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows if rows and "pair" in rows[0] else []


def log_rows(rows):
    log = config.LOG_FILE
    if log.exists():
        with log.open(encoding="utf-8") as f:
            header = f.readline().strip().split(",")
        if header != LOG_FIELDS and "pair" in header:  # older log version - add the new columns
            old = read_log()
            with log.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=LOG_FIELDS, restval="", extrasaction="ignore")
                w.writeheader()
                w.writerows(old)
        elif header != LOG_FIELDS:  # single-pair log from an old version - move it aside
            log.rename(log.with_name(f"decisions_old_{datetime.now():%Y%m%d%H%M%S}.csv"))
    new = not log.exists()
    with log.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)
