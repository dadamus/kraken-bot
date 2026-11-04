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
   strength versus BTC.
2. Gathers context from public sources (no keys needed; a failing source is simply skipped):
   news headlines (CoinDesk, Cointelegraph, Decrypt), perpetual funding and open interest
   (Kraken Futures), Fear & Greed (alternative.me), BTC/ETH dominance and stablecoin share (CoinGecko),
   options implied volatility DVOL (Deribit, BTC and ETH), plus the settlement of the bot's own earlier calls.
3. Sends everything to Claude in one request. Claude returns, for every pair, `buy` / `sell` / `hold`,
   a confidence, the probability that the price is higher in 3 days (`p_up_3d`), a stop level and a
   short justification. The response format is enforced with structured outputs.
4. **Code decides**: minimum confidence, position-size and exposure limits, stop placement, the drawdown halt.
5. Executes (simulated in paper mode, market orders on Kraken in live mode) and appends one row per pair
   to `decisions.csv`.

**Every 15 minutes** (configurable) it checks stop-loss and take-profit on the current price and sells
the whole position if one is hit. This uses only Kraken's public price API, never the model, so it
costs nothing. It also refreshes the Home Assistant entities.

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
| `sensor.kraken_bot_decision` | time of the last decision | summary, decisions with reasoning, the last 30 log rows |
| `sensor.kraken_bot_performance` | 3-day forecast hit rate, % | full performance breakdown |

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

Exposure limits only restrict **new** buys; an existing position above a limit is never sold because of it.

**Stops.** With every buy the model gives `stop_price`, the price at which its thesis no longer holds.
Code keeps it at least 1 ATR below the price (so ordinary daily noise does not shake the position out)
and no further than `stop_loss_pct`; without a level it uses 2 ATR. With an open position the model may
only raise the stop, never lower it. Coins without a stop (e.g. bought outside the bot) get the first one
the model gives. The fixed −8% stop from the entry price stays active as a backstop.

These are not stop orders on the exchange: they are checked every `ha_refresh_minutes`, so in a fast
crash, or while the bot is not running, the sale happens at a worse price.

**After a sale** the EUR stays on the account. Buying back is decided by the next daily cycle, within the
limits above. The model sees the stop sales of the last 7 days and is told not to buy back without a new reason.

## Measuring performance

Without measurement there is no way to tell whether the model adds anything. So:

- every call is settled after 1, 3 and 7 days: the average price change after `buy` / `hold` / `sell`
  (good calls: higher after `buy` than after `sell`);
- `p_up_3d` forecasts are scored with the direction hit rate, the Brier score (0.25 = coin flip, lower is
  better) and calibration (do ~0.6 forecasts come true ~60% of the time?);
- benchmarks with fees since the first decision: **buy and hold** and **trend** (in the market while the daily
  close is above SMA50). If the bot does not beat them over a longer period, the model adds no value.

You can see this in `--report`, in the log of every cycle, in `sensor.kraken_bot_performance` and in the
dashboard's "Performance" section. Expect to need a few dozen settled forecasts (1-2 months) before drawing
conclusions; the pairs are strongly correlated.

## Going live (after a few weeks of paper trading)

1. Kraken → Settings → API → create a key with **Query Funds**, **Query Open Orders & Trades**,
   **Query Closed Orders & Trades** and **Create & Modify Orders**. **Never Withdraw Funds.**
2. Put the keys into the add-on options (or `.env`), set `"mode": "live"` in `config.json`, enable the
   `live` option (or `LIVE=true`). Keep `"live_validate_only": true` at first: Kraken validates the orders
   but does not place them, and the log says "validation only".
3. Check the balance in the log and in Home Assistant. Real orders start after you set
   `"live_validate_only": false`. Start with a small `max_trade_eur`.

In live mode the bot reads the real EUR and coin balances at the start of every cycle and refresh, and
computes the entry price from the trade history (last 500 trades). Live state and log are separate:
`state_live.json`, `decisions_live.csv`. The return is measured from the equity at the first sync.

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
pip install -r requirements-dev.txt
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

## Repository layout

| Path | What |
|---|---|
| `kraken_bot/bot.py` | The whole bot |
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
