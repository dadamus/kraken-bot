# Changelog

## 0.1.0
- First version: hourly **breakout** and **reversion** rules on bitcoin, gold and EUR/USD, a simulated CFD
  account (the CFD paper add-on's mechanics and XTB costs), paper only.
- The day's regime per instrument (`trend_up`, `trend_down`, `range`, `no_trade`) decides which rules may
  trade. Two books on the same candles: **model** (Claude, once a day) and **rules** (SMA20 / SMA50 and the
  daily ADX), to measure what the model adds.
- Rules in code: a cost filter (the target at least 4x the spread), 0.5 % risk per trade, 24-hour maximum hold,
  2 % daily loss limit, 4 trades per pair a day, no entries an hour around high-impact US releases, flat before
  the weekend for gold and EUR/USD, a trailing stop for breakouts after +1R.
- A backtest of the rules on the last 720 hourly candles (or Kraken's OHLCVT files) on the start and weekly.
- Panel: regimes, the last hourly step per pair, positions, trades, results per strategy and per day before
  and after costs, the regimes settled, the backtest. Home Assistant entities and a daily summary
  notification.
