# Changelog

## 0.2.1
- From the CFD paper 0.5.0 copy: spike wicks of Kraken's thin EUR/USD book are cut in the daily candles
  (the regime rule, the daily trend, the model's view) and in the candles of the stop check, so a spike no
  broker shows no longer fires a stop. The hourly candles of the strategies are left as they are. The CFD
  paper's shared risk budget is off here (no `max_open_risk_pct` in the intraday config).

## 0.2.0
- No reversion long in a daily downtrend (both books): when the last daily close is below the 50-day SMA and
  that SMA is lower than 10 days before, the range regime only fades rallies with shorts. In the first two
  weeks the rules book lost 10 of its 11 reversion longs, all in such a downtrend. Settings
  `strategies.reversion.no_long_in_daily_downtrend` (default true), `daily_sma` (50), `daily_slope_days` (10).
- The model sees `daily_downtrend` per pair and is told about the rule. A backtest of the rules book over
  the last 30 days (the same period the rule was drawn from, so optimistic): -0.9 % without it, +4.8 % with it.
- Takes effect from the next daily cycle (it computes the daily trend).

## 0.1.1
- The copy of the CFD paper package is updated (the CFD paper's break-even stop). It is off here: the
  intraday config has no `breakeven_r`, and the breakout strategy keeps its own trailing stop.

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
