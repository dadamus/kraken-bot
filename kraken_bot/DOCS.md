# Kraken bot

An experimental crypto bot for [Kraken](https://www.kraken.com). Once a day it sends market data,
indicators and news for a few pairs to Claude (Anthropic), gets a buy / sell / hold assessment per pair,
and code executes it under fixed risk rules. Every few minutes it checks stop-loss / take-profit on the
current price, without the model.

**Paper trading by default.** Read the full README before going live:
https://github.com/dadamus/kraken-bot

## Quick start

1. Put your Anthropic API key in the **Configuration** tab and pick a **language** (English or Polish).
2. Start the add-on and enable **Start on boot** and **Watchdog**.
3. On the first start the add-on creates `config.json` in `/addon_configs/<id>_kraken_bot/`
   (paper mode, 1000 virtual EUR). Edit it with the Samba share, Studio Code Server or File editor
   add-on to change pairs and risk rules - no rebuild needed.
4. Add the dashboard: `dashboards/ha_dashboard.yaml` (English) or `dashboards/ha_dashboard.pl.yaml`
   (Polish) from the repository, pasted into a new dashboard's raw configuration editor.

## Options

| Option | Meaning |
|---|---|
| `anthropic_api_key` | Key from console.anthropic.com |
| `kraken_api_key` / `kraken_api_secret` | Live mode only. Permissions: Query Funds, Query Open Orders & Trades, Query Closed Orders & Trades, Query Ledger Entries (deposits/withdrawals are not counted as profit), Create & Modify Orders. Never "Withdraw Funds" |
| `language` | `en` or `pl`: logs, entity names, notifications and the model's reasoning |
| `run_time` | Time of the daily cycle (HH:MM, local) |
| `ha_refresh_minutes` | How often stops are checked and entities refreshed (no model calls) |
| `live` | Trade on the real Kraken balance (also needs `"mode": "live"` in `config.json`) |
| `entry_prices` | Purchase price per 1 coin for coins bought outside the bot (0 = unknown) |

## Entities

`sensor.kraken_bot_equity`, `sensor.kraken_bot_return`, `sensor.kraken_bot_free_eur`,
`sensor.kraken_bot_decision`, `sensor.kraken_bot_performance` and one per pair
(`sensor.kraken_bot_xbteur`, ...). Every decision also goes to the Logbook. Set
`home_assistant.notify` in `config.json` (e.g. `notify.mobile_app_my_phone`) to get a push
notification on every buy or sell.

This is an experiment, not investment advice. You can lose money.
