# Changelog

## 0.1.1
- The image no longer downloads the Kraken bot's package from GitHub while building (the install failed when
  GitHub's archive service answered 503). The add-on folder now carries a copy of it, kept identical by
  `scripts/sync_krakenbot.py` and a test.

## 0.1.0
- First version, paper only: a daily long / short / flat call from Claude on the Kraken bot's data, a
  simulated CFD account (leverage, spread, overnight swap, margin stop-out), stops and take-profits replayed
  on 1-minute candles, risk-based position size, drawdown halt.
- Measuring: trades by side after costs, calls settled after 3 days, `p_up_3d` Brier, the return against
  holding the coins.
- Read-only panel (ingress), Home Assistant entities, push notifications for every simulated trade and for
  failures. English and Polish.
