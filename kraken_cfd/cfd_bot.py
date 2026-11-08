#!/usr/bin/env python3
"""CFD paper simulator: once a day Claude calls long / short / flat per pair, a simulated CFD account executes
the calls with leverage, spread and overnight swap. Paper only - no broker, no orders, no keys besides
Anthropic's.

  python cfd_bot.py              # the daily cycle
  python cfd_bot.py --offline    # fake data and random calls (no internet, no API key)
  python cfd_bot.py --refresh    # stops / take-profits, swap, stop-out, Home Assistant
  python cfd_bot.py --report     # the latest evaluation
"""
from cfd.cycle import main

if __name__ == "__main__":
    main()
