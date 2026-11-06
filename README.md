# Kraken bot

[![tests](https://github.com/dadamus/kraken-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/dadamus/kraken-bot/actions/workflows/tests.yml)

An experimental crypto trading bot for [Kraken](https://www.kraken.com) that asks Claude (Anthropic)
for a daily market assessment and lets **hard-coded risk rules have the final word**. It runs as a
Home Assistant add-on or as a standalone Docker container, in **paper mode by default**.

> **This is an experiment, not investment advice.** Crypto is volatile and an LLM has no proven edge
> in predicting short-term prices. The bot measures its own track record precisely so you can see
> whether it adds anything over simply holding. Only trade with money you can afford to lose.

Logs, Home Assistant entities, notifications and the model's reasoning are available in
**English (default)** and **Polish**.

## How it works

**Once a day** (default 08:05) the bot:

1. Downloads up to 720 daily candles from Kraken for each pair in `pairs` (e.g. BTC, ETH, SOL in EUR)
   and computes indicators: RSI, SMA7/30/50/200, ATR, 30-day and 1-year ranges, 26 weekly candles,
   strength versus BTC. Plus the last 24 hourly candles, so the model sees the intraday path.
2. Gathers context from public sources (no keys needed; a failing source is simply skipped):
   news headlines (CoinDesk, Cointelegraph, Decrypt), perpetual funding and open interest
   (Kraken Futures), Fear & Greed (alternative.me), BTC/ETH dominance and stablecoin share (CoinGecko),
   options implied volatility DVOL (Deribit, BTC and ETH), US markets - Nasdaq 100, S&P 500, VIX, 10-year
   yield, dollar index (FRED), this week's high-impact US macro events (ForexFactory feed), the Coinbase
   premium versus Kraken (US spot demand), plus the settlement of the bot's own earlier calls.
3. Sends everything to Claude in one request. Claude returns, for every pair, `buy` / `sell` / `hold`,
   a confidence, the probability that the price is higher in 3 days (`p_up_3d`), a stop level and a
   short justification. The response format is enforced with structured outputs.
4. **Code decides**: minimum confidence, position-size and exposure limits, stop placement, the drawdown halt.
5. Executes (simulated in paper mode, market orders on Kraken in live mode) and appends one row per pair
   to `decisions.csv`.

**Every 15 minutes** (configurable) it checks stop-loss and take-profit on the current price and sells
the whole position if one is hit. This uses only Kraken's public price API, so it costs nothing. It also
refreshes the Home Assistant entities.

**Extra cycles.** The refresh asks the model between the daily cycles only when something happened:
a stop / take-profit fired at least 3 hours ago (was it a real break or a wick?), or a pair moved at
least 1.5 daily ATRs from the price of the last cycle. At most 3 extra cycles a day and at least 3 hours
after any cycle; the model is told why it is being asked. See `extra_cycles` below.

## Install as a Home Assistant add-on

Requires Home Assistant OS or Supervised (the add-on store).

1. **Settings → Add-ons → Add-on store → ⋮ → Repositories**, add
   `https://github.com/dadamus/kraken-bot` and close the dialog.
2. Find **Kraken bot** in the store and click **Install**. The image is built on your device, which
   takes a few minutes.
3. **Configuration** tab: enter `anthropic_api_key` (from [console.anthropic.com](https://console.anthropic.com)),
   choose `language` and the daily `run_time`. Kraken keys and `live` are only needed for live trading.
4. **Info** tab: **Start**, then enable **Start on boot** and **Watchdog**. Watch the **Log** tab.
5. On the first start the add-on creates `config.json` (paper mode, 1000 virtual EUR) in
   `/addon_configs/<id>_kraken_bot/`. Edit it with the **Samba share**, **Studio Code Server** or
   **File editor** add-on to change pairs and risk rules. Changes apply on the next run, no rebuild needed.
6. **Dashboard**: Settings → Dashboards → Add dashboard → New dashboard from scratch → open it →
   pencil → ⋮ → **Raw configuration editor**, then paste the whole
   [`dashboards/ha_dashboard.yaml`](dashboards/ha_dashboard.yaml) (English) or
   [`dashboards/ha_dashboard.pl.yaml`](dashboards/ha_dashboard.pl.yaml) (Polish).
   The price and forecast charts use the [ApexCharts card](https://github.com/RomRider/apexcharts-card):
   install it from Settings → Marketplace (HACS on Home Assistant before 2026.11), search "apexcharts-card",
   then reload the browser. Without it only those three
   cards show an error.
7. **Push notifications** (optional): install the Home Assistant Companion app on your phone, find your
   service in Developer tools → Actions (e.g. `notify.mobile_app_my_phone`) and put it in `config.json`
   as `"home_assistant": {"notify": "notify.mobile_app_my_phone", ...}`. You get a notification on every
   buy and sell, including stop-loss sales.

Updates: when a new version appears, Home Assistant offers **Update** on the add-on page.

<details>
<summary>Alternative: local add-on without the repository</summary>

Copy the [`kraken_bot/`](kraken_bot) folder to `/addons/kraken_bot/` on your Home Assistant (Samba share
or Studio Code Server), then Add-on store → ⋮ → **Check for updates**. The add-on appears under
"Local add-ons". Do not put a `config.json` in that folder: the Supervisor would read it as the add-on
manifest. The bot's data then lives in `/addon_configs/local_kraken_bot/`.
</details>

### Home Assistant entities

| Entity | State | Attributes |
|---|---|---|
| `sensor.kraken_bot_equity` | equity in EUR | mode, start, free EUR, halt, last refresh |
| `sensor.kraken_bot_return` | return since start, % | |
| `sensor.kraken_bot_free_eur` | free EUR | |
| `sensor.kraken_bot_<pair>` | position value in EUR | coins, price, entry price and its source, stop, position result, buy and hold |
| `sensor.kraken_bot_<pair>_forecast` | expected 3-day change, % (theoretical, see below) | last 60 daily closes, the 3-day path with a ±1σ band, p_up_3d, time of the forecast; updated by the daily cycle (until then a refresh builds it from the latest call in the log) |
| `sensor.kraken_bot_decision` | time of the last decision | summary, decisions with reasoning, the last 30 log rows |
| `sensor.kraken_bot_performance` | 3-day forecast hit rate, % | full performance breakdown, incl. stops and daily vs extra cycles |
| `sensor.kraken_bot_api_cost` | model API cost this month, USD (estimate from list prices) | calls and tokens this month, totals, the last call |

The forecast line on the charts is theoretical. The model only gives the probability that the price is
higher in 3 days (`p_up_3d`), not a target. Assuming a normal 3-day return with the current daily volatility,
that probability implies an expected move: 50% is a flat line, 60% with 2% daily volatility is about +0.9%.
The band is ±1σ around it, where about 2 in 3 outcomes should land. It is a picture of what the model said,
not an extra signal: the bot does not trade on it.

Every decision is also written to the Logbook. Entities set through the REST API do not survive a
Home Assistant restart; the add-on notices and republishes them within a minute.

## Run standalone with Docker

Any Linux server, NAS or PC with Docker and Docker Compose:

```bash
git clone https://github.com/dadamus/kraken-bot.git
cd kraken-bot
cp .env.example .env    # put at least ANTHROPIC_API_KEY in it; set TZ, RUN_TIME, BOT_LANGUAGE
docker compose up -d --build
docker compose logs -f
```

The scheduler is the same as in the add-on: the daily cycle at `RUN_TIME` and the stop-loss check every
`REFRESH_MINUTES`. Data is kept in `./data`; on the first start a paper-mode `data/config.json` is created
for you to edit. To publish entities to an existing Home Assistant as well, set
`home_assistant.enabled: true` and `home_assistant.url` in `data/config.json` and put a long-lived access
token in `.env` as `HA_TOKEN`.

Without Docker (Python 3.12+):

```bash
pip install -r kraken_bot/requirements.txt
python kraken_bot/bot.py --offline   # dry run on random data, no internet or keys
python kraken_bot/bot.py             # one real cycle in paper mode (needs data/config.json and ANTHROPIC_API_KEY)
python kraken_bot/bot.py --report    # bot vs buy-and-hold and the model's track record
python kraken_bot/bot.py --ha        # stop-loss / take-profit check on the current price (no model)
```

Copy `kraken_bot/config.example.json` to `data/config.json` first; keys can go in `data/.env` or the
environment. Schedule `bot.py` daily and `bot.py --ha` every 15 minutes with cron, or just run
`kraken_bot/addon.py`, which does the scheduling.

## Configuration

### Add-on options / environment variables

| Add-on option | Docker variable | Default | Meaning |
|---|---|---|---|
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | | Required |
| `kraken_api_key`, `kraken_api_secret` | `KRAKEN_API_KEY`, `KRAKEN_API_SECRET` | | Live mode only |
| `language` | `BOT_LANGUAGE` | `en` | `en` or `pl`; overrides `language` in `config.json` |
| `run_time` | `RUN_TIME` | `08:05` | Daily cycle (local time, `TZ`) |
| `ha_refresh_minutes` | `REFRESH_MINUTES` | `15` | Stop-loss / take-profit check and entity refresh |
| `stop_check_seconds` | `STOP_CHECK_SECONDS` | `15` | Cheap price check between refreshes; a breached stop / take-profit sells at once (`0` = off) |
| `live` | `LIVE` | `false` | Trade on the real balance (also needs `"mode": "live"`) |
| `entry_prices` | `ENTRY_PRICES` (JSON) | | Purchase price per 1 coin for coins bought outside the bot |

### `config.json`

| Key | Default | Meaning |
|---|---|---|
| `mode` | `paper` | `live` is required (together with the `live` option) for real trading |
| `language` | `en` | `en` / `pl` |
| `pairs` | `XBTEUR`, `ETHEUR`, `SOLEUR` | Kraken pair names; all must be quoted in the same currency. They share one budget |
| `model` | `claude-opus-5-5` | Claude model |
| `effort` | `high` | Reasoning effort: `low` / `medium` / `high` / `xhigh` / `max` |
| `paper_start_eur` | `1000` | Virtual starting capital in paper mode |
| `fee_pct`, `slippage_pct` | `0.4`, `0.1` | Simulated costs (also a reserve for live buys) |
| `live_validate_only` | `true` | In live mode Kraken only validates orders without placing them |
| `entry_prices` | `{}` | Same as the add-on option, e.g. `{"XBTEUR": 65617}` |
| `news_feeds` | 3 RSS feeds | Headline sources |
| `home_assistant` | | `enabled`, `url` (standalone only), `notify` (e.g. `notify.mobile_app_my_phone`) |
| `risk` | | See below |

### Risk rules (`risk`)

| Setting | Default | What it does |
|---|---|---|
| `min_confidence` | 0.65 | Weaker signals are ignored |
| `max_trade_eur` | 200 | Maximum single buy |
| `min_trade_eur` | 10 | Smaller orders are skipped |
| `max_exposure_pct` | 60 | Maximum % of equity in crypto in total |
| `max_exposure_per_pair_pct` | 30 | Maximum % of equity in one pair |
| `stop_loss_pct` | 8 | Sell the whole position at −8% from the entry price; also the maximum distance of the model's stop |
| `take_profit_pct` | 15 | Sell the whole position at +15% from the entry price (`0` disables it) |
| `max_drawdown_pct` / `halt_days` | 20 / 7 | Equity down 20% from the start → no new buys for a week (stops keep working) |
| `intraday_stops` | `true` | Check stops on every refresh, not only once a day |
| `exchange_stops` | `true` | Live mode: keep the stop as a stop-loss order on Kraken (needs Cancel & Close Orders) |

Exposure limits only restrict **new** buys; an existing position above a limit is never sold because of it.

### Extra cycles (`extra_cycles`, optional)

| Setting | Default | What it does |
|---|---|---|
| `enabled` | `true` | Ask the model between the daily cycles when an event below happens |
| `after_stop_hours` | 3 | Hours after a stop / take-profit sale before the extra cycle |
| `move_atr` | 1.5 | Price move since the last cycle, in daily ATR14, that starts an extra cycle |
| `min_gap_hours` | 3 | Minimum time since the last cycle of any kind |
| `max_per_day` | 3 | Maximum extra cycles per day (each is one model call) |

**Stops.** With every buy the model gives `stop_price`, the price at which its thesis no longer holds.
Code keeps it at least 1 ATR below the price (so ordinary daily noise does not shake the position out)
and no further than `stop_loss_pct`; without a level it uses 2 ATR. With an open position the model may
only raise the stop, never lower it. Coins without a stop (e.g. bought outside the bot) get the first one
the model gives. The fixed −8% stop from the entry price stays active as a backstop.

**In live mode the stop also rests on Kraken** as a stop-loss order (`exchange_stops`, on by default; not
in validation mode). It covers the whole position at the higher of the model's stop and the fixed
`stop_loss_pct` level, so the position is protected even when Home Assistant, the add-on or the internet
is down. The bot moves the order when the stop or the position changes, cancels it before selling by
itself, and logs a fill as a `stop` row ("STOP on the exchange"). Only orders it placed itself (marked
with its own `userref`) are touched. It needs the **Cancel & Close Orders** permission on the API key.
Take-profit stays with the bot.

Besides that the container compares the public price with the levels every `stop_check_seconds` (15 s by
default) and sells with a market order as soon as one is breached; with that check off they are only
checked every `ha_refresh_minutes`. With a stop resting on Kraken this check is the backup: it sells only
when the price is more than 1% below the order's level, i.e. the exchange did not sell. A stop-loss order
also becomes a market sell once triggered, so in a fast crash the sale can still happen below the level.

**After a sale** the EUR stays on the account. Buying back is decided by the next cycle (the extra one a
few hours after the stop, or the daily one), within the limits above. The model sees the stop sales of the last 7 days and is told not to buy back without a new reason.

## Measuring performance

Without measurement there is no way to tell whether the model adds anything. So:

- every call is settled after 1, 3 and 7 days: the average price change after `buy` / `hold` / `sell`
  (good calls: higher after `buy` than after `sell`);
- `p_up_3d` forecasts are scored with the direction hit rate, the Brier score (0.25 = coin flip, lower is
  better) and calibration (do ~0.6 forecasts come true ~60% of the time?);
- benchmarks with fees since the first decision: **buy and hold** and **trend** (in the market while the daily
  close is above SMA50). If the bot does not beat them over a longer period, the model adds no value;
- every automatic stop / take-profit sale is settled the same way: the price 1, 3 and 7 days after the sale
  (lower = the stop saved money, higher = it sold too early);
- the forecast scores are also split into daily and extra cycles, to see whether the extra cycles help;
- the cost of every model call (tokens, USD at list prices, with the model that actually answered) is
  summed per month - compare it with what the bot earns.

You can see this in `--report`, in the log of every cycle, in `sensor.kraken_bot_performance` and in the
dashboard's "Performance" section. Expect to need a few dozen settled forecasts (1-2 months) before drawing
conclusions; the pairs are strongly correlated.

## Going live (after a few weeks of paper trading)

1. Kraken → Settings → API → create a key with **Query Funds**, **Query Open Orders & Trades**,
   **Query Closed Orders & Trades**, **Query Ledger Entries**, **Create & Modify Orders** and
   **Cancel & Close Orders** (moving the stop order resting on Kraken).
   **Never Withdraw Funds.**
2. Put the keys into the add-on options (or `.env`), set `"mode": "live"` in `config.json`, enable the
   `live` option (or `LIVE=true`). Keep `"live_validate_only": true` at first: Kraken validates the orders
   but does not place them, and the log says "validation only".
3. Check the balance in the log and in Home Assistant. Real orders start after you set
   `"live_validate_only": false`. Start with a small `max_trade_eur`.

In live mode the bot reads the real EUR and coin balances at the start of every cycle and refresh, and
computes the entry price from the trade history (last 500 trades). Live state and log are separate:
`state_live.json`, `decisions_live.csv`. The return is measured from the equity at the first sync.
Deposits and withdrawals (EUR and coins of the configured pairs) are read from the Kraken ledger and move
that reference point, so money you add is not counted as profit and money you take out is not a loss
(nor does it trigger the drawdown halt). This needs the **Query Ledger Entries** permission; without it
the log warns on every sync. Recorded flows are kept in `cash_flows` in `state_live.json`.

**Important:** the bot treats the whole EUR balance and all coins of the configured pairs on the account
as its own and may sell them (e.g. on a stop-loss). Use a dedicated Kraken account or sub-account, or
keep only the bot's money there.

Coins bought outside the trade history (e.g. "Buy crypto" in the Kraken app, or deposits) have no entry
price, so the fixed stop-loss / take-profit cannot work for them. Enter the average purchase price per coin
in `entry_prices`. If that price is already more than `stop_loss_pct` above the current price (or
`take_profit_pct` below), the next refresh sells the whole position; the log warns about it on sync.

Taxes: selling crypto is usually a taxable event. Keep your Kraken trade history.

## Development and tests

```bash
pip install -r kraken_bot/requirements.txt -r requirements-dev.txt
python -m pytest tests -q
```

The tests run without internet or keys: every test gets its own temporary directory, and network calls
(Kraken, Claude) are blocked and replaced with fakes. They cover the indicators, the Claude request and
response parsing, the risk rules, the 15-minute guard (paper, live validation, rejected orders),
performance settlement, the log and its migrations, syncing with Kraken, full `--offline` / `--ha` /
`--live` runs, the container entry point, both languages, and both dashboards' templates.

The same inside the add-on image (Python and library versions as in Home Assistant):

```bash
docker build -t kraken-bot kraken_bot
docker run --rm -v "$PWD:/src:ro" kraken-bot sh -c 'pip install -q pytest jinja2 pyyaml && cd /tmp && cp -r /src/tests /src/kraken_bot /src/dashboards . && python -m pytest tests -q -p no:cacheprovider'
```

On Windows with Git Bash, prefix it with `MSYS_NO_PATHCONV=1` and use `$(pwd -W)` instead of `$PWD`.

### Translations

Every message the user reads lives in `kraken_bot/krakenbot/locales/<code>.toml`, with `en.toml` as the
reference. To add a language, copy `en.toml` to e.g. `de.toml`, set `language_name = "German"` (the model
is told to answer in it) and translate the values, keeping the `{placeholders}`. A missing key falls back
to English; the tests check that every file has the same keys and placeholders as `en.toml`. For the
add-on, also add the code to `language: list(en|pl)` in `config.yaml` and a file to `translations/`
(the add-on options screen).

## Repository layout

| Path | What |
|---|---|
| `kraken_bot/bot.py` | Command-line entry point (`--offline`, `--report`, `--ha`, `--live`) |
| `kraken_bot/krakenbot/` | The bot itself, one module per concern (module map in `__init__.py`): market data, indicators, Claude, risk rules, Kraken, guard, evaluation, Home Assistant |
| `kraken_bot/krakenbot/locales/` | Texts the user reads, one TOML file per language |
| `kraken_bot/krakenbot/system_prompt.txt` | The model's system prompt (always English; the answer language is filled in) |
| `kraken_bot/addon.py` | Scheduler / container entry point (add-on and Docker) |
| `kraken_bot/config.yaml`, `Dockerfile`, `translations/`, `DOCS.md` | The Home Assistant add-on |
| `kraken_bot/config.example.json` | Default configuration, copied to the data folder on the first start |
| `dashboards/` | Home Assistant dashboards (English, Polish) |
| `docker-compose.yml`, `.env.example` | Standalone Docker |
| `repository.yaml` | Makes this repository installable from the Home Assistant add-on store |
| `tests/` | pytest suite |
| `SECURITY.md` | How to report a vulnerability |

Data files (never committed): `config.json`, `state.json` / `state_live.json` (portfolio: free EUR and the
position in each pair; delete to start over), `decisions.csv` / `decisions_live.csv` (decision log with the
model's reasoning).

## License

[GNU General Public License v3.0](LICENSE). You may use, modify and share it; distributed modified
versions must stay open source under the same license. No warranty: you are responsible for any trades the
bot makes on your account.
