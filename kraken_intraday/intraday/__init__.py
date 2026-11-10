"""Intraday paper: rule-based strategies on hourly candles, the day's regime per instrument set once a day,
a simulated CFD account executing the trades. Paper only - nothing is ever sent to a broker. The question it
answers: can hourly trading earn more than its costs, and does Claude's daily judgement help?

Two books run side by side on the same candles, with the same strategies and rules:
  model   the regime comes from Claude, once a day
  rules   the regime comes from a fixed rule on daily candles (SMA20/SMA50 and ADX)
The difference between them is what the model adds.

Modules, roughly in the order a run uses them:
  settings    paths, config.json (merged over config.example.json)
  ta          indicators on hourly candles (EMA, ATR, RSI, Bollinger bands, channel, ADX)
  regime      the four regimes, what each allows, the rule for the rules book
  strategies  the breakout and the reversion signal, the cost filter, the trailing stop
  engine      the state (two books), the hourly step: exits, blocks, entries
  model       the daily regime call to Claude
  journal     regimes.csv (every daily regime) and trades.csv (every closed trade)
  report      trade statistics, daily results, the regime settlement, benchmarks
  backtest    the strategies replayed on past hourly candles with the rule's regime
  ha          Home Assistant entities and notifications
  texts       English / Polish texts for the panel and notifications
  panel       the read-only dashboard page
  cycle       command line, the daily cycle and the refresh (which runs the hourly step)

The CFD mechanics (spread, leverage, swap, weekend hours, margin stop-out, stops on minute candles, position
size, drawdown halt) come from the CFD paper add-on's package (cfd), market data and the Home Assistant client
from the Kraken bot's (krakenbot). The add-on folder carries copies of both (kept identical by
scripts/sync_krakenbot.py and a test), because Home Assistant builds each add-on from its own folder only.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _package, _folder in (("krakenbot", "kraken_bot"), ("cfd", "kraken_cfd")):
    try:
        __import__(_package)
    except ImportError:  # running from the repository without the copies: the packages live next door
        sys.path.insert(0, str(_ROOT / _folder))
