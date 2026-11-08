"""CFD paper: Claude calls long / short / flat on a few crypto pairs and a simulated CFD account executes the
calls with leverage, spread, overnight swap and a margin stop-out. Paper only - nothing is ever sent to a
broker. The point is to measure whether the model's short side (and leverage) would be worth anything.

Modules, roughly in the order a cycle uses them:
  settings   paths, config.json (merged over config.example.json)
  account    the simulated CFD account: fills with spread, P&L, swap, margin, stop-out
  guard      stops and take-profits between cycles, checked on minute candles
  risk       the rules in code: confidence, stop and take-profit placement, position size, drawdown halt
  model      the prompt, the response schema and the API call
  journal    calls.csv (every call) and trades.csv (every closed position)
  report     settling the calls, trade statistics, benchmarks
  ha         Home Assistant entities and notifications
  texts      English / Polish texts for the panel and notifications
  panel      the read-only dashboard page
  cycle      command line, the daily cycle and the refresh

Market data, indicators, cost tracking and the Home Assistant client come from the Kraken bot's package
(krakenbot). The add-on folder carries a copy of it (kraken_cfd/krakenbot, kept identical by
scripts/sync_krakenbot.py and a test), because Home Assistant builds each add-on from its own folder only.
"""
import sys
from pathlib import Path

try:
    import krakenbot  # noqa: F401
except ImportError:  # running from the repository: the package lives next door
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "kraken_bot"))
