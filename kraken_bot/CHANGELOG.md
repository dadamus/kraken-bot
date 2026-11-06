# Changelog

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
