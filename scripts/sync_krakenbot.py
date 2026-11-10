#!/usr/bin/env python3
"""Copies the shared packages into the add-ons that use them.

Home Assistant builds every add-on from its own folder only, so:
- kraken_cfd and kraken_intraday carry a copy of the Kraken bot's package (kraken_bot/krakenbot: market data,
  indicators, cost tracking, the Home Assistant client),
- kraken_intraday also carries a copy of the CFD paper package (kraken_cfd/cfd: the simulated CFD account).
Run this after changing kraken_bot/krakenbot or kraken_cfd/cfd; the shared-copy tests fail while a copy differs.

  python scripts/sync_krakenbot.py
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPIES = [("kraken_bot/krakenbot", "kraken_cfd/krakenbot"),
          ("kraken_bot/krakenbot", "kraken_intraday/krakenbot"),
          ("kraken_cfd/cfd", "kraken_intraday/cfd")]


def main():
    for source, target in COPIES:
        src, dst = ROOT / source, ROOT / target
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"copied {source} -> {target}")


if __name__ == "__main__":
    main()
