# Kraken CFD paper

**Paper only.** An experiment next to the Kraken bot: once a day Claude calls **long / short / flat** for a
few crypto pairs, and a *simulated* CFD account executes the calls with leverage, spread and an overnight
swap. No broker, no orders, no exchange keys - only the Anthropic key. The question it answers: would the
model's calls, the short side included, be worth anything after the costs a CFD broker charges?

The Kraken bot is not touched: this add-on has its own model call, its own data folder and its own panel.
It reads only public market data (the same sources the Kraken bot uses).

## Quick start

1. **Configuration** tab: the Anthropic API key, the language, the daily `run_time` (default 08:20, a few
   minutes after the Kraken bot so the two do not hit the same public APIs at once).
2. Start the add-on, enable **Start on boot** and **Watchdog**.
3. On the first start `config.json` appears in `/addon_configs/<id>_kraken_cfd/` (1000 virtual EUR,
   leverage 2). Edit it to change pairs, costs and rules; changes apply on the next run.
4. **CFD paper** in the sidebar: the account, open positions, the model's calls, closed trades and the
   track record. Read-only.

## How it works

**Daily cycle** (`run_time`): the same data the Kraken bot's model gets (daily, weekly and hourly candles
with indicators, news, funding and open interest, Fear & Greed, dominance, implied volatility, US markets,
the macro calendar, the Coinbase premium), plus the simulated account and the trading costs. The model
answers per pair: `long`, `short`, `flat` (close / stay out) or `hold`, with a confidence, `p_up_3d`, a stop
and a take-profit. Code decides:

| Rule | Default | |
|---|---|---|
| `risk.min_confidence` | 0.65 | needed to open; closing never needs it |
| `risk.risk_per_trade_pct` | 1.5 | the size loses this share of equity at the stop |
| `risk.min_stop_atr` / `max_stop_pct` / `default_stop_atr` | 1 / 10 / 2 | where the stop may be (the model's, kept in this band) |
| `risk.default_reward_risk` | 2 | take-profit when the model gives none or one closer than the stop |
| `risk.max_margin_per_trade_pct` / `max_margin_pct` | 25 / 60 | margin caps, % of equity |
| `risk.min_trade_eur` | 20 | smaller positions are skipped |
| `risk.max_drawdown_pct` / `halt_days` | 25 / 7 | no new positions after this drawdown from the peak |
| `allow_short` | true | `false` = long / flat only (for comparison) |

A call for the other direction closes the open position first; with enough confidence it then opens the
new one. `hold` or the same direction may move the stop closer, never further.

**Refresh** (every `refresh_minutes`): stops and take-profits are replayed on Kraken's 1-minute candles
since the last check, as a broker's resting orders would fire (a wick counts; stop and take-profit in one
candle = the stop; a gap fills at the candle's open). Then the overnight swap and the margin stop-out.

## Costs (check them against your broker)

| Setting | Default | Meaning |
|---|---|---|
| `leverage` | 2 | margin = notional / leverage (2:1 is the EU retail limit for crypto CFDs) |
| `spread_pct` | BTC 0.3, ETH 0.3, SOL 0.5, other 1.0 | the whole spread in % of the price; half is paid on entry, half on exit |
| `swap_pct_per_day` | long 0.097222, short 0.027778 | overnight financing in % of the notional per night held |
| `stop_out_margin_level_pct` | 50 | positions are closed (worst first) when equity falls below this % of the margin |

The defaults come from XTB's instrument specification table (Poland, November 2026): the upper end of
the "target spread" range (BTC and ETH 0.1-0.3 %, SOL 0.25-0.5 %; in volatile markets it can be wider) and
the crypto CFD swap of -0.097222 % a day for a long and -0.027778 % for a short. Shorts are cheaper to hold
than longs. Brokers change these; check the table (xtb.com -> Specification -> Crypto) now and then and
update `config.json`.

## Measuring

The panel and `sensor.kraken_cfd_performance` show: the return against holding the same coins (equal
split, no leverage, no costs - a generous benchmark), trades split into long and short (count, win rate,
net result after costs, average R), every call settled 3 days later (the move in the call's direction, the
hit rate per direction) and the `p_up_3d` Brier score (0.25 = a coin flip). Command line inside the
container: `python cfd_bot.py --report`.

## Entities and notifications

`sensor.kraken_cfd_equity`, `sensor.kraken_cfd_<pair>` (long / short / flat with the position),
`sensor.kraken_cfd_decision`, `sensor.kraken_cfd_performance`, `sensor.kraken_cfd_api_cost`.
With `home_assistant.notify` (e.g. `notify.mobile_app_my_phone`) every simulated open and close is a push
notification (`notify_trades: false` turns them off); failed cycles and refreshes are alerted the same way.

## Model cost

One request a day, the same size as one daily cycle of the Kraken bot (`model`, `effort` in
`config.json`). There are no extra cycles. The panel shows this month's cost.
