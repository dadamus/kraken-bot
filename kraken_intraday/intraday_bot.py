#!/usr/bin/env python3
"""Intraday paper simulator: rule-based strategies on hourly candles, the day's regime per instrument from
Claude (model book) and from a fixed rule (rules book), a simulated CFD account executing the trades. Paper
only - no broker, no orders, no keys besides Anthropic's.

  python intraday_bot.py               # the daily cycle
  python intraday_bot.py --offline     # fake data and random regimes (no internet, no API key)
  python intraday_bot.py --refresh     # stops / take-profits, swap, stop-out; the hourly step after a new candle
  python intraday_bot.py --report      # the latest evaluation
  python intraday_bot.py --backtest    # the strategies on past hourly candles (--csv PAIR=FILE for more)
"""
from intraday.cycle import main

if __name__ == "__main__":
    main()
