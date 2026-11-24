# Changelog

## 0.5.1
- US market data from Yahoo Finance, FRED only as the fallback (from the Kraken bot 2.11.3). FRED is a day
  or more behind: on Tuesday morning the model saw Thursday's Nasdaq and Friday's VIX, while yields and the
  dollar are the main drivers of gold and EUR/USD. The dollar is DXY now (`usd_index_dxy`); FRED's broad index
  comes only as the fallback, as `usd_index_broad`.
- The evaluation, the panel and the model's track record show the p_up_3d Brier next to the base rate: how
  often the price rose (`up_rate_pct`) and the Brier of always forecasting that share (`brier_base_rate`). In
  a falling market a high hit rate comes almost by itself (89 % on 2026-11-24).

## 0.5.0
- Spike wicks: Kraken's EUR/USD book is thin and its candles show wicks no broker's feed does (1.0952 ->
  1.1859 on 2026-11-20). Such a stop fired the EUR/USD short that day at 1.104, while the market traded around
  1.096. A wick longer than `max_wick` (5) median candle ranges is now cut, in the daily candles the model
  and the ATR see and in the minute candles of the stop check. EUR/USD's ATR drops from 1.15 % to about
  0.75 %. Off for gold and crypto (`instruments.<pair>.max_wick`).
- A shared risk budget: `risk.max_open_risk_pct` (6) caps what all open positions together lose at their
  stops; a new position gets only what is left. A stop at break-even risks nothing, so it frees the budget.
  The model sees `max_open_risk_pct` and `account.open_risk_eur`. With `risk_per_trade_pct: 3` that is two
  full positions at a time, so calls on one theme (a strong dollar) no longer add up to 9 %.
- The model is told that gold's weekend candles are PAXG token trading, not the gold market, so a weekend
  low is not a gold level.

## 0.4.0
- Break-even stop: once a position has gone `risk.breakeven_r` (1) times its initial risk in its favour,
  the stop moves to the level where closing gets the entry price back after the spread (the swap is not
  covered). The refresh checks it on every minute candle; the move counts from the next candle, and a stop
  that is already tighter is kept. The model sees `breakeven_r` in `trading_terms` and `stop_at_breakeven`
  per position. `breakeven_r: 0` turns it off.

## 0.3.0
- Wider stops before high-impact US releases: within `risk.event_window_hours` (48: the cycle runs once a
  day, so a position opened the day before a release is covered too) of a CPI, FOMC, payrolls or similar
  release in the macro calendar, a stop is at least `risk.event_stop_atr` (2) daily ATRs from
  the price instead of `min_stop_atr`. This applies to new positions and to a stop the model moves closer.
  The risk in EUR stays `risk_per_trade_pct`, so the position is smaller. The model sees the release and
  the minimum in `trading_terms` (`release_ahead`, `min_stop_atr_now`). `event_window_hours: 0` turns it off.

## 0.2.0
- Bitcoin, gold and EUR/USD instead of three coins: one instrument per asset class, so the calls are more
  independent. Gold comes from Kraken's `PAXGEUR`, EUR/USD from `EURUSD`.
- Per-instrument settings in `config.json` `instruments`: leverage (crypto 1:2, gold 1:20, EUR/USD 1:30),
  spread and swap from XTB's table, the quote currency (USD prices are converted to EUR) and weekend hours.
  While a market is closed nothing is traded and no stop fires; a gap on the reopen fills at the reopen price.
- The prompt covers all three asset classes: which context applies to crypto only, what moves gold and
  EUR/USD, judging moves against each instrument's own ATR. The model sees every instrument's terms and
  whether its market is open.
- The holding benchmark counts EUR/USD as holding EUR.
- An existing `config.json` keeps its pairs; add the `instruments` block from `config.example.json` to switch.

## 0.1.4
- The model is no longer told the account is a simulation: the prompt and `trading_terms` describe it as a
  trading account. The add-on itself stays paper only.

## 0.1.3
- `style` in `config.json`: `cautious` (default, as before) or `aggressive` - the model looks for a trade in
  every pair every day, in either direction, with tight stops and larger targets. The model also sees the
  style in `trading_terms`. DOCS.md has a matching aggressive preset for the `risk` rules.

## 0.1.2
- Default costs from XTB's specification table instead of estimates: spread BTC 0.3 %, ETH 0.3 %, SOL 0.5 %
  (the upper end of the target spread), swap -0.097222 % a day for longs and -0.027778 % for shorts.
  An existing `config.json` keeps its own values; edit it to change them.

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
