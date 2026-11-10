# Kraken intraday paper

**Paper only.** The third experiment next to the Kraken bot and the CFD paper add-on: fixed rules trade
bitcoin, gold and EUR/USD on **hourly** candles, and once a day the **regime** decides which rules may trade
each instrument. A *simulated* CFD account (the CFD paper add-on's: spread, leverage, overnight swap, weekend
hours, margin stop-out) executes the trades. No broker, no orders, no exchange keys - only the Anthropic key.

Two books run side by side, with the same strategies, rules and candles:

| Book | The day's regime comes from |
|---|---|
| **model** | Claude, once a day (`run_time`) |
| **rules** | a fixed rule on daily candles: trend up when the close is above SMA50, SMA20 is above SMA50 and the daily ADX is at least 20; trend down the mirror; otherwise range |

The questions it answers: **can hourly trading earn more than its costs**, and **does Claude's daily
judgement beat a simple rule?** Hourly moves are small - BTC's typical hour is about 0.5 %, a CFD spread
0.3 % - so the costs decide most of it. The panel shows every result before and after costs.

The other add-ons are not touched: this one has its own model call, data folder and panel, and reads only
public market data.

## Quick start

1. **Configuration** tab: the Anthropic API key, the language, the daily `run_time` (default 07:45, before
   the European session; keep it a few minutes away from the other add-ons' cycles).
2. Start the add-on, enable **Start on boot** and **Watchdog**.
3. On the first start `config.json` appears in `/addon_configs/<id>_kraken_intraday/` (1000 virtual EUR per
   book, bitcoin, gold, EUR/USD), and a **backtest** of the rules on the last 30 days of hourly candles runs -
   its result is in the panel within a minute.
4. **Intraday paper** in the sidebar: today's regimes, the last hourly step per pair, open positions,
   closed trades, results per strategy and per day, the regimes settled, the backtest. Read-only.

## How it works

**Daily cycle** (`run_time`): the rule sets the rules book's regimes (no API, so the rules book keeps
trading if the model call fails), then Claude sets the model book's. The model sees the daily and hourly
picture (hourly ATR, ADX, RSI, distance to the 50-hour EMA, band width, the spread as a share of the hourly
range), the costs, the macro calendar, news and the market context the other add-ons use, its recent regimes
with what followed, and its own track record. It answers per instrument:

| Regime | What may trade |
|---|---|
| `trend_up` | **breakout** longs only |
| `trend_down` | **breakout** shorts only |
| `range` | **reversion** both ways |
| `no_trade` | nothing new; an open position is closed |

A regime with confidence below `risk.min_confidence` (0.5) counts as `no_trade` for new entries. A regime
older than `intraday.regime_max_age_hours` (30) counts as `no_trade` too (a failed cycle does not leave a book
trading on yesterday's view), but does not close positions.

**Hourly step** (the first refresh after every full hour, on the candle that has just closed), per book and
pair:

1. *Exits*: after `max_hold_hours` (24) - `TIME`; within the hour before the weekend close of gold or EUR/USD
   (`flat_before_weekend`) - `WEEKEND`; when today's regime no longer allows the position - `REGIME`. A
   breakout position that is 1R in profit trails its stop `trail_atr` hourly ATRs behind the best close.
2. *Blocks*: regime, market hours, the drawdown halt, `max_daily_loss_pct` (2 % from the day's start: no new
   trades for the rest of the day), `max_trades_per_day` (4 per pair), a high-impact US release from
   `blackout_before_min` (60) before to `blackout_after_min` (60) after, no recent hourly candle.
3. *Signal*:
   - **breakout**: the hourly close breaks above the highest high of the previous `channel_hours` (20) candles
     (below the lowest low for a short), the hour before was still inside (a fresh breakout), and the close is
     on the right side of the `ema_hours` (50) EMA. Stop `stop_atr` (1.5) hourly ATRs away, take-profit
     `reward_risk` (2) x the stop.
   - **reversion**: the hourly close falls below the lower Bollinger band (`band_hours` 20, `band_std` 2) with
     RSI (`rsi_hours` 14) below `rsi_low` (30) - a long; the mirror above the upper band with RSI above
     `rsi_high` (70) - a short. Take-profit at the middle band, stop `stop_atr` (1.5) hourly ATRs away.
   - **The cost filter**: the take-profit must be at least `min_target_to_cost` (4) x the instrument's
     round-trip spread away. With the default costs that is 1.2 % for bitcoin, 0.12 % for gold and 0.06 % for
     EUR/USD - bitcoin trades only on big hours.
4. *Size*: the stop loses `risk.risk_per_trade_pct` (0.5 %) of equity, capped by `max_margin_per_trade_pct`
   (25) and `max_margin_pct` (60); below `min_trade_eur` (20) nothing opens. One position per pair and book.

**Refresh** (every `refresh_minutes`, default 5): stops and take-profits replayed on Kraken's 1-minute candles
since the last check, as a broker's resting orders would fire (one request per pair for both books), the
overnight swap, the margin stop-out, and the hourly step when a new hourly candle has closed.

**Backtest** (on the add-on's start, then weekly; `python intraday_bot.py --backtest` in the container): the
strategies with the rule's regime replayed on the last 720 hourly candles (30 days, the most Kraken's API
returns). The model's regime cannot be replayed honestly - it already knows what happened after - so the
backtest tests the rules, and the two books test the model. For a longer test use Kraken's downloadable
OHLCVT files (the 60-minute file per pair, copied to the add-on's config folder):

```
python intraday_bot.py --backtest --csv XBTEUR=/config/XBTEUR_60.csv --csv PAXGEUR=/config/PAXGEUR_60.csv
```

Simplifications: the entry fills at the signal candle's close, stops on hourly highs and lows (the stop first
when both are inside one candle), no blackout around releases. A 30-day result is a direction, not proof.

## Instruments and costs

The same instruments and XTB costs as the CFD paper add-on (see its documentation for the sources): bitcoin
`XBTEUR` (1:2, spread 0.3 %, 24/7), gold `PAXGEUR` (1:20, 0.03 %, closed Fri 23:00 - Mon 00:00), `EURUSD`
(1:30, 0.015 %, closed Fri 22:00 - Sun 23:00), each with its overnight swap. `config.json` has the same
`instruments` block; change it there.

## Measuring

The panel and `sensor.kraken_intraday_performance`, per book: equity and return against holding the
instruments, today's result and the green / red days (only completed days), trades per strategy, pair and
side (win rate, result **after and before costs**, average R, average hours, exit reasons), every regime
settled against the next regime's price (trend_up right when the price rose, trend_down when it fell, range
when the move stayed within one daily ATR). `python intraday_bot.py --report` prints the same.

What to look at after a few weeks:
- **before vs after costs**: if the strategies earn before costs and lose after, the hourly scale is too
  small for these costs - not a model problem;
- **model book vs rules book**: the model must beat the rule by more than its API cost to be worth it;
- **per instrument**: bitcoin is expected to struggle with a 0.3 % spread; gold and EUR/USD are where
  intraday trading can pay at all.

## Entities and notifications

`sensor.kraken_intraday_equity` (model book) and `sensor.kraken_intraday_rules_equity`,
`sensor.kraken_intraday_regime` (today's regimes and the last hourly step), `sensor.kraken_intraday_performance`,
`sensor.kraken_intraday_api_cost`. With `home_assistant.notify` (e.g. `notify.mobile_app_my_phone`) yesterday's
result of both books arrives after the daily cycle (`daily_summary`); every model-book trade only with
`notify_trades: true` (off by default: it trades several times a day). Failed cycles and refreshes are alerted.

## Model cost

One request a day (`model`, `effort` in `config.json`), about the size of the CFD add-on's - the hourly
decisions are rules and cost nothing. The panel shows this month's cost.
