# Kraken CFD paper

**Paper by default.** An experiment next to the Kraken bot: once a day Claude calls **long / short / flat** for
bitcoin, gold and crude oil (one instrument per asset class), and a *simulated* CFD account executes the calls with leverage, spread and an overnight
swap. No broker, no orders, no exchange keys - only the Anthropic key. The question it answers: would the
model's calls, the short side included, be worth anything after the costs a CFD broker charges? Optionally
it shadows every step on Kraken Derivatives (orders checked, never sent) or trades there for real - see
[Kraken Derivatives: shadow and live](#kraken-derivatives-shadow-and-live).

The Kraken bot is not touched: this add-on has its own model call, its own data folder and its own panel.
It reads only public market data (the same sources the Kraken bot uses).

## Quick start

1. **Configuration** tab: the Anthropic API key, the language, the daily `run_time` (default 08:20, a few
   minutes after the Kraken bot so the two do not hit the same public APIs at once).
2. Start the add-on, enable **Start on boot** and **Watchdog**.
3. On the first start `config.json` appears in `/addon_configs/<id>_kraken_cfd/` (1000 virtual EUR,
   bitcoin, gold, WTI crude oil). Edit it to change pairs, costs and rules; changes apply on the next run.
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
| `risk.max_open_risk_pct` | 6 | all open positions together may lose at most this share of equity at their stops (a stop at break-even counts 0); a new position gets only what is left, or is not opened. `null` = off |
| `risk.min_stop_atr` / `max_stop_pct` / `default_stop_atr` | 1 / 10 / 2 | where the stop may be (the model's, kept in this band) |
| `risk.event_stop_atr` / `event_window_hours` | 2 / 48 | minimum stop in ATRs while a high-impact US release (CPI, FOMC, payrolls) is this close; also for a stop the model moves closer. The size shrinks, the risk in EUR stays. `event_window_hours: 0` = off |
| `risk.default_reward_risk` | 2 | take-profit when the model gives none or one closer than the stop |
| `risk.breakeven_r` | 1 | after this many R (the initial risk, entry to stop) in profit the stop moves to break-even: closing there gets the entry price back after the spread (the swap is not covered). Checked on every candle between cycles; `0` = off |
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
EUR; thin trading on Kraken, so an odd minute wick can touch a stop) and crude oil as `WTIUSD`: Kraken has
no spot market for oil, so it is the mark price of its derivatives contract `PF_WTIOILUSD` (a 24/7 index of
WTI, in USD per barrel; `"source": "futures"` in the instrument). Over the last 180 days oil moved against
bitcoin and gold (daily correlation -0.2 and -0.3), with bitcoin-like daily ranges. Its history starts with
the contract on 2026-05-28, so SMA200 and the yearly range stay empty until there are enough days.

Until 0.6.0 the third instrument was `EURUSD`. Kraken does not offer currency contracts to EEA clients, so
it could never go live there; it is still defined in `instruments` and can be put back in `pairs`.

| Instrument | Leverage | Spread (round trip) | Swap long / short a day | Hours (local) |
|---|---|---|---|---|
| `XBTEUR` | 1:2 | 0.3 % | 0.097222 % / 0.027778 % | 24/7 |
| `PAXGEUR` (gold) | 1:20 | 0.03 % | 0.034556 % / 0.012667 % | closed Fri 23:00 - Mon 00:00 |
| `WTIUSD` (oil) | 1:10 | 0.08 % | 0 / 0 | 24/7 (the contract's index) |
| `EURUSD` (not in `pairs`) | 1:30 | 0.015 % | 0.008333 % / -0.000003 % (a tiny credit) | closed Fri 22:00 - Sun 23:00 |

The values come from XTB's instrument specification table (Poland, November 2026): crypto takes the upper
end of the "target spread" (BTC 0.1-0.3 %), gold and EUR/USD about 1.5x the typical spread (0.75 USD on
gold, 0.00012 on EUR/USD). Oil is priced like Kraken's contract: its spread (0.05-0.09 % in the first samples) and no
overnight swap (a broker's oil CFD has none either, only an adjustment when the futures roll over; Kraken's
hourly funding shows up in the shadow mode's numbers). Leverage is the EU retail limit (1:10 for commodities
other than gold). Brokers change these; check the table (xtb.com -> Specification) now and then.

In `config.json` each instrument has an entry in `instruments`: `name`, `asset_class` (`crypto` gets the
crypto-only context and `vs_btc`), `quote` (`EUR` or `USD`; USD prices are converted with EUR/USD),
`leverage`, `spread_pct`, `swap_pct_per_day`, `source` (`"futures"`: prices from the contract in
`futures_symbols`, else Kraken's spot market) and, for markets with a weekend break, `weekend_close` /
`weekend_open` (e.g. `"fri 22:00"`), and `max_wick`: a wick longer than this many median candle ranges is cut before the indicators and the stop check (Kraken's EUR/USD book is thin and shows spikes no broker does; default 5 for currencies, 0 = off for gold and crypto). Anything missing falls back to the top-level `leverage`, `spread_pct`
and `swap_pct_per_day`. While a market is closed no order is placed and no stop fires; a stop beyond the
first price after the reopen fills at that price (a gap). The overnight swap is charged per calendar day,
which over a week equals the broker's triple Wednesday or Friday charge.

`stop_out_margin_level_pct` (50): positions are closed (worst first, only where the market is open) when
equity falls below this % of the margin.

## Kraken Derivatives: shadow and live

The add-on option **`execution`** says where the calls go:

| `execution` | What happens |
|---|---|
| `paper` (default) | the simulated account only, as before |
| `shadow` | the simulated account exactly as before, **plus** every step it takes (open, close, stop, take-profit, a stop moved) turned into the order Kraken Derivatives would need and checked against Kraken - nothing is sent |
| `live` | real orders on Kraken Derivatives. Needs **also** `"mode": "live"` in `config.json` (two opt-ins) and a key with Full Access |

Kraken has no CFDs; its EEA derivatives (Payward Europe Digital Solutions, CySEC) are perpetual futures quoted
in USD. A pair trades the contract in `instruments.<pair>.futures_symbols` (the first one; the others are only
measured), else `PF_<base>USD`: bitcoin `PF_XBTUSD`, gold `PF_PAXGUSD` (also measured: `PF_XAUUSD`, a 24/7 gold
index). **There is no EUR/USD for EEA clients** (Kraken's currency perpetuals exclude EU residents), so live
mode refuses to start until `EURUSD` is removed from `pairs`. EEA retail leverage is at most 10x; live mode
uses the lower of `config.json`'s leverage and Kraken's. Costs on Kraken: a taker fee of 0.05 % per side and an
hourly funding payment instead of an overnight swap.

**Shadow mode** (`execution: shadow`):
- `shadow.csv` in the data folder: per step the contract, side, size rounded to the contract (0.0001 BTC,
  0.001 oz), the price the order book gives now and the slippage against the paper fill in basis points, the
  stop / take-profit carried over to the contract's price (same distance in %), the margin at Kraken's
  leverage, and why the order would not go through (below the minimum size, not offered to EEA clients, more
  margin than the wallet has free).
- Every refresh samples the contracts' spread and funding; the panel shows their averages next to the
  paper's spread and swap.
- Every daily cycle re-prices **all closed paper trades** with Kraken's costs: the move between the paper's
  mid prices, minus the taker fee on both sides, the measured spread and the funding Kraken actually charged
  over the holding time (its public hourly history, about a year). Trades of a pair Kraken cannot trade in
  the EEA are counted apart. This is the number to look at before going live: `--report`, the panel's
  Kraken section, `sensor.kraken_cfd_kraken`.
- With a derivatives key (**Read Only** is enough: `kraken_futures_api_key` / `_secret`, made in the Kraken
  derivatives account under Settings > API, separate from the spot key) the wallet's value and free margin are
  checked hourly, which also proves the key and the signing work. A key that stops working is an alert.

**Live mode** (`execution: live` and `"mode": "live"`), the rules:
- its own files: `state_live.json`, `calls_live.csv`, `trades_live.csv` (prices in the contract's USD,
  `fee_eur` / `funding_eur`; `spread_eur` / `swap_eur` hold the same fee and funding for the statistics). The
  paper account's files stay as the experiment's record and are no longer updated.
- the exchange is the source of truth: every run reads the wallet, the positions and the resting orders. The
  return is measured from the wallet's value at the first live run; a transfer into or out of the
  derivatives wallet moves that reference instead of counting as profit or loss.
- a position opens with a market order, then a **stop and a take-profit rest on Kraken** as reduce-only
  orders triggered on the mark price (one trade in a thin book cannot fire them). **A position whose stop
  cannot be placed is closed at once** and alerted. The model's tighter stop and the break-even move are
  edits of the resting order (never loosened; break-even = the entry price plus the fees of both sides).
- a position gone from Kraken was closed there (stop, take-profit, liquidation): the fills give the exit.
  A position the bot did not open is left alone and alerted.
- the bot's own rules stay: the stop-out at `stop_out_margin_level_pct`, the drawdown halt, the risk limits.
- a failed order is `error` in `executed`, with the reason in the rule and an alert; a flip whose close failed
  does not open the other side.
- switching `execution` away from `live` with positions open does not abandon them: their stops stay on
  Kraken, the bot records their closes and sends only orders that reduce a position.

**Before going live:** switch on **`live_smoke_test`** once (with a Full Access key). At start the add-on buys
and sells 0.0001 BTC on `PF_XBTUSD` (about 5 EUR, fees of a few cents) using every order type live mode
uses - market order, resting stop and take-profit, a stop edit, cancels, a reduce-only close - and reports
PASSED or FAILED (details in the log; it always tries to leave no position and no order behind). Switch the
option off afterwards. There is no public test environment on Kraken any more, so this is the only check
against the real API.

**Do not switch on Kraken's Unified Wallet** while the Kraken bot runs: it would make the spot bot's balance
the collateral of these positions.

## Measuring

The panel and `sensor.kraken_cfd_performance` show: the return against holding the same instruments
(equal split, no leverage, no costs - a generous benchmark; a currency pair counts as holding EUR, oil by its
USD price; an instrument added later joins from its first price), trades split into long and short (count, win rate,
net result after costs, average R), every call settled 3 days later (the move in the call's direction, the
hit rate per direction) and the `p_up_3d` Brier score (0.25 = a coin flip). Command line inside the
container: `python cfd_bot.py --report`.

## Entities and notifications

`sensor.kraken_cfd_equity`, `sensor.kraken_cfd_<pair>` (long / short / flat with the position),
`sensor.kraken_cfd_decision`, `sensor.kraken_cfd_performance`, `sensor.kraken_cfd_api_cost`, and in shadow
mode `sensor.kraken_cfd_kraken` (the closed paper trades with Kraken's costs, the measured spreads and
funding, the wallet check).
With `home_assistant.notify` (e.g. `notify.mobile_app_my_phone`) every simulated open and close is a push
notification (`notify_trades: false` turns them off); failed cycles and refreshes are alerted the same way.

## Model cost

One request a day, the same size as one daily cycle of the Kraken bot (`model`, `effort` in
`config.json`). There are no extra cycles. The panel shows this month's cost.
