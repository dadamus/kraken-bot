#!/usr/bin/env python3
"""Copies kraken_bot/krakenbot into kraken_cfd/krakenbot.

Home Assistant builds every add-on from its own folder only, so the CFD paper add-on carries a copy of the
Kraken bot's package (market data, indicators, cost tracking, the Home Assistant client). Run this after
changing kraken_bot/krakenbot; tests/cfd_paper/test_shared_copy.py fails while the copy differs.

  python scripts/sync_krakenbot.py
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kraken_bot" / "krakenbot"
TARGET = ROOT / "kraken_cfd" / "krakenbot"


def main():
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"copied {SOURCE.relative_to(ROOT)} -> {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
