# Changelog

## 2.11.0
- Buying back after a sale: while the bot is out of a coin it sold in the last 30 days (by the model or a
  stop), the model sees the sale in `portfolio.positions` (`last_exit`: date, price, model / stop, and the
  current price versus the exit). Buying back above the exit means the sale lost money versus holding, so
  the prompt asks the model to look first for a confirmation at or below the exit price (a higher low, a
  failed breakdown back above the broken level) instead of only a level well above it. It is still told not
  to buy into a falling market. Before, it only saw sales from the last 7 days of calls and set e.g. "buy
  above 68 300" after a stop sale at 67 420.

## 2.10.1
- Fair comparison with doing nothing (live): the bot is now compared with simply keeping the coins and
  EUR the account held when the bot started, over the same period and with the same deposits and
  withdrawals. The panel tile "Bot vs holding" shows the difference in percentage points and in EUR.
  Before, the bot's result (since the live start, deposits included) was set against an equal split of all
  pairs since the first decision - a different period and coins the bot never held. That benchmark stays in
  the performance section, labelled "equal split".
- An existing live state rebuilds the starting holdings once from the Kraken trade history.
- Deposits and withdrawals are also added to the "keep" benchmark; Home Assistant gets `hold_return_pct`,
  `vs_hold_pp` and `vs_hold_eur` on `sensor.kraken_bot_return`.

## 2.10.0
- The model says what it is waiting for: with every call a concrete condition (`watch`, e.g. "buy if BTC
  holds above 68 500 for several hours") and optional price levels above / below the current price.
- Crossing such a level starts an extra cycle (within the usual limits: 3 h gap, 3 a day), in which the model
  checks with fresh data whether the confirmation came; it is not an order by itself. Switch off with
  `extra_cycles.watch_levels: false`.
- The panel shows the condition on the pair's card (🎯), the model's levels in the extra-cycle band and on the
  chart, and in the decision log. The log gets a `watch` column; the model sees its earlier conditions.

## 2.9.0
- Alerts to the phone (`home_assistant.notify`) when the bot needs attention: a failed model cycle (daily or
  extra, with the error), refreshes failing three times in a row (and when they work again), or a stop order
  that cannot be placed or checked on Kraken. At most every 6 hours per problem, with a "resolved" message.
- A failed extra cycle no longer fails the whole refresh: it is logged, alerted and not retried at once.
- Trailing stop: the stop follows the highest price since the purchase, `trailing_stop_atr` daily ATRs below
  it (default 2, `0` = off). Only raises the stop; the order on Kraken follows.

## 2.8.0
- The add-on's own dashboard: **Kraken bot** in the Home Assistant sidebar (ingress). Status, equity and
  the bot versus buy and hold, per pair the position, the stop (🛡️ when it rests on Kraken), the model's
  latest call and a chart with the forecast, the bot's trades and the extra-cycle band; the market and the
  macro calendar, performance and the decision log. Read-only, follows the add-on language and light /
  dark mode, nothing to install. Standalone Docker: `http://localhost:8099`.
- The cycle keeps what the model saw (sentiment, US markets, Coinbase premium, funding) for the panel.

## 2.7.0
- Live mode: the stop also rests on Kraken as a stop-loss order for the whole position, so it protects
  the position even when Home Assistant, the add-on or the internet is down. The bot places it, moves it
  when the stop or the position changes, cancels it before selling by itself and logs a fill as a stop
  row. Only its own orders (own `userref`) are touched. **Give the API key the Cancel & Close Orders
  permission.** Switch off with `"exchange_stops": false` in `risk`; not used in validation mode.
- With a stop resting on Kraken the bot's own 15-second check is the backup: it sells only when the price
  is more than 1% below the order's level. Take-profit stays with the bot.

## 2.6.0
- Stops are settled like the model's calls: the price 1, 3 and 7 days after every automatic stop /
  take-profit sale (lower = the stop saved money). In `--report`, the cycle log, the performance entity
  and the model's own track record.
- The forecast scores are split into daily and extra cycles; the decision log gets a `cycle` column
  (`daily`, `extra:stop`, `extra:move`, `guard`). Older rows count as daily.
- Model API cost: tokens and an estimate in USD (list prices of the model that answered) for every call,
  summed per month. New entity `sensor.kraken_bot_api_cost`; also in `--report` and the cycle log.
- The cycle count in the evaluation no longer includes stop sales.
- Dashboard: a model API cost tile, and in Performance the stop sales with the price after them, daily
  vs extra cycles and the model cost. Re-paste the dashboard after updating.

## 2.5.0
- More context for the model from outside crypto (free, no keys; a failing source is skipped):
  - `us_markets`: Nasdaq 100, S&P 500, VIX, the US 10-year yield and the broad dollar index from FRED,
    with 1/5/20-day changes,
  - `macro_calendar`: this week's high-impact US events (Fed, CPI, jobs...) with the time until them,
    from the ForexFactory feed; kept in the state and downloaded at most every 6 hours,
  - `coinbase_premium`: BTC on Coinbase versus Kraken in USD (now, 24 h average, last 6 hours).

## 2.4.0
- Extra model cycles between the daily ones, started by events instead of the clock: a stop /
  take-profit sale at least 3 hours ago, or a price move of at least 1.5 daily ATRs since the last cycle.
  At most 3 a day, at least 3 hours apart; the model is told why it is asked (`this_cycle`). Tune or
  switch off with `extra_cycles` in `config.json` (on by default).
- The model also gets the last 24 hourly candles per pair (`last_24_hours`), e.g. to see whether a broken
  level was reclaimed after a stop.
- The model's recent calls cover the last 7 days, not the last 7 cycles.

## 2.3.1
- Charts appear without waiting for the daily cycle: when a pair has no chart data yet, the
  refresh builds it from public Kraken candles and the model's latest call in the log (no model call,
  no orders).
- Docs: the ApexCharts card comes from Settings → Marketplace on Home Assistant 2026.11+.

## 2.3.0
- Dashboard: a price chart per pair (last 30 days) with the model's theoretical 3-day forecast - the path
  implied by `p_up_3d` and the current volatility, with a ±1σ band - plus the entry price and the stop.
  Needs the ApexCharts card from HACS; re-paste the dashboard after updating.
- New entities `sensor.kraken_bot_<pair>_forecast` (expected 3-day change in %), updated by the daily
  cycle; the charts appear after the first cycle on this version.

## 2.2.1
- Internal: the bot is split into the `krakenbot` package (one module per concern) and the
  messages moved to `locales/en.toml` / `pl.toml`. No change in behaviour, options or data files.

## 2.2.0
- Stops react within seconds: between refreshes the add-on checks the public price every
  `stop_check_seconds` (default 15) and sells as soon as a stop-loss / take-profit level is breached,
  instead of waiting up to `ha_refresh_minutes`. Set it to 0 for the old behaviour.

## 2.1.0
- Live mode: deposits and withdrawals are read from the Kraken ledger and move the return/drawdown
  reference point, so adding money no longer shows up as profit. Give the API key the
  **Query Ledger Entries** permission (otherwise the log only warns). Flows are tracked from the first
  sync after the update; adjust `start_eur` by hand for earlier ones.

## 2.0.0
- First public release: English by default, Polish as an option (`language`).
- Home Assistant entity IDs are now English: `sensor.kraken_bot_equity`, `_return`, `_free_eur`,
  `_decision`, `_performance` (plus one per pair). Re-paste the dashboard after updating.
- Installable from the GitHub repository; also runs standalone with Docker.
- First start creates a paper-mode `config.json` automatically.
