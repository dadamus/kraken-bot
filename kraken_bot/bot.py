#!/usr/bin/env python3
"""
Kraken crypto bot: once a day it analyses the market and news for a few pairs, asks Claude for an
assessment, and code executes the decision under fixed risk rules.

Default: PAPER TRADING (simulation, no real money).

Usage:
  python bot.py              # one cycle in paper mode
  python bot.py --offline    # test on fake data (no internet, no API keys)
  python bot.py --report     # performance summary
  python bot.py --live       # real orders (requires "mode": "live" in config.json + API keys)
  python bot.py --ha         # refresh: stop-loss / take-profit on the current price (no model) + Home Assistant
  python bot.py --ha --live  # the same with the real Kraken balance

The code lives in the krakenbot package next to this file; see krakenbot/__init__.py for the module map.
"""
from krakenbot.cli import main

if __name__ == "__main__":
    main()
