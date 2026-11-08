# Kraken CFD paper

**Paper only.** An experiment next to the Kraken bot: once a day Claude calls **long / short / flat** for
bitcoin, gold and EUR/USD (one instrument per asset class), and a *simulated* CFD account executes the calls with leverage, spread and an overnight
swap. No broker, no orders, no exchange keys - only the Anthropic key. The question it answers: would the
model's calls, the short side included, be worth anything after the costs a CFD broker charges?

The Kraken bot is not touched: this add-on has its own model call, its own data folder and its own panel.
It reads only public market data (the same sources the Kraken bot uses).

## Quick start

1. **Configuration** tab: the Anthropic API key, the language, the daily `run_time` (default 08:20, a few
   minutes after the Kraken bot so the two do not hit the same public APIs at once).
2. Start the add-on, enable **Start on boot** and **Watchdog**.
3. On the first start `config.json` appears in `/addon_configs/<id>_kraken_cfd/` (1000 virtual EUR,
   bitcoin, gold, EUR/USD). Edit it to change pairs, costs and rules; changes apply on the next run.
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

**Style** (`style`): `cautious` (default) tells the model that flat is a normal answer and a weak trade is
worse than none; `aggressive` asks it to look for a setup in every pair every day, in either direction,
with tight stops and targets 2-4x the stop. Confidence and `p_up_3d` stay honest either way (they are
scored). The rules in code are set separately; an aggressive preset to go with it:

```json
"style": "aggressive",
"risk": {"min_confidence": 0.55, "risk_per_trade_pct": 3, "max_margin_per_trade_pct": 45,
         "max_margin_pct": 100, "default_reward_risk": 3, "max_drawdown_pct": 50, "halt_days": 3}
```

With three positions at 3 % risk each, one bad day can cost about 9 % of equity (more when they are one
bet on the dollar), and full margin use brings the 50 % stop-out within reach of a sharp move. That is the point of the experiment, not a bug.

A call for the other direction closes the open position first; with enough confidence it then opens the
new one. `hold` or the same direction may move the stop closer, never further.

**Refresh** (every `refresh_minutes`): stops and take-profits are replayed on Kraken's 1-minute candles
since the last check, as a broker's resting orders would fire (a wick counts; stop and take-profit in one
candle = the stop; a gap fills at the candle's open). Then the overnight swap and the margin stop-out.

## Instruments and costs (check them against your broker)

Three instruments from different asset classes, so the calls are more independent than three coins that move
together. Prices come from Kraken: bitcoin `XBTEUR`, gold as `PAXGEUR` (a token backed by 1 troy ounce, in
EUR; thin trading on Kraken, so an odd minute wick can touch a stop) and `EURUSD`.

| Instrument | Leverage | Spread (round trip) | Swap long / short a day | Hours (local) |
|---|---|---|---|---|
| `XBTEUR` | 1:2 | 0.3 % | 0.097222 % / 0.027778 % | 24/7 |
| `PAXGEUR` (gold) | 1:20 | 0.03 % | 0.034556 % / 0.012667 % | closed Fri 23:00 - Mon 00:00 |
| `EURUSD` | 1:30 | 0.015 % | 0.008333 % / -0.000003 % (a tiny credit) | closed Fri 22:00 - Sun 23:00 |

The values come from XTB's instrument specification table (Poland, November 2026): crypto takes the upper
end of the "target spread" (BTC 0.1-0.3 %), gold and EUR/USD about 1.5x the typical spread (0.75 USD on
gold, 0.00012 on EUR/USD). Leverage is the EU retail limit. Brokers change these; check the table
(xtb.com -> Specification) now and then.

In `config.json` each instrument has an entry in `instruments`: `name`, `asset_class` (`crypto` gets the
crypto-only context and `vs_btc`), `quote` (`EUR` or `USD`; USD prices are converted with EUR/USD),
`leverage`, `spread_pct`, `swap_pct_per_day` and, for markets with a weekend break, `weekend_close` /
`weekend_open` (e.g. `"fri 22:00"`). Anything missing falls back to the top-level `leverage`, `spread_pct`
and `swap_pct_per_day`. While a market is closed no order is placed and no stop fires; a stop beyond the
first price after the reopen fills at that price (a gap). The overnight swap is charged per calendar day,
which over a week equals the broker's triple Wednesday or Friday charge.

`stop_out_margin_level_pct` (50): positions are closed (worst first, only where the market is open) when
equity falls below this % of the margin.

## Measuring

The panel and `sensor.kraken_cfd_performance` show: the return against holding the same instruments
(equal split, no leverage, no costs - a generous benchmark; EUR/USD counts as holding EUR), trades split into long and short (count, win rate,
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
