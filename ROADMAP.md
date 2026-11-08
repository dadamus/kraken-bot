# Roadmap

Where the project stands, what comes next and - just as important - what has to be true before the next
step. The bot trades real money, so every phase ends with a check, not with a feature list.

Last updated: 2026-11-08 (version 2.10.0).

## Principles

1. **Safety before strategy.** Protection (stops, limits, alerts) comes first; it must work even when the
   model, Home Assistant or the internet does not.
2. **Measure before tuning.** No change to the strategy (prompt, thresholds, position sizes) without data
   from the bot's own settlement: calls, forecasts, stops, cycles and costs.
3. **The model must earn its cost.** If after a fair period the bot does not beat simple benchmarks (buy and
   hold, the SMA50 trend rule) after fees and API cost, the model is not adding value - and that is a valid
   result, not a failure of the project.
4. **Small, reversible steps.** One change at a time, a switch to turn it off, tests for every rule.

## Done (2.0 - 2.10)

| Area | What |
|---|---|
| Core | Claude-assisted daily cycle for BTC, ETH, SOL; risk rules in code with the final word; paper, validation and live modes; Home Assistant add-on and standalone Docker; English and Polish |
| Data for the model | Daily, weekly and hourly candles with indicators; news; funding and open interest; Fear & Greed; dominance and stablecoin share; options implied volatility; US markets (Nasdaq, S&P 500, VIX, yields, dollar); the week's US macro calendar; Coinbase premium; its own track record |
| Reacting | Extra model cycles on events (a stop fired, a move of 1.5 ATR, a level from the model's own entry condition); stop checks every 15 s |
| Protection | Model stop plus a fixed stop-loss; stop-loss orders resting on Kraken; trailing stop; drawdown halt; exposure limits; alerts to the phone when a cycle fails, refreshes fail or a stop order cannot be placed |
| Measuring | Every call, forecast (Brier, calibration) and stop sale settled after 1/3/7 days; daily vs extra cycles; benchmarks with fees; model API cost per call and month; deposits and withdrawals kept out of the result |
| Seeing | The add-on's own dashboard panel (ingress); YAML dashboards and entities for Home Assistant |

## Now: stabilise and observe (until about 2026-11-21)

Two weeks without new trading features. A lot was added in a short time; first it has to prove itself in
real use.

Checks:

- [ ] The first stop order appears on Kraken after the first buy (🛡️ in the panel, the order in the app).
- [ ] The trailing stop raises the stop in a rally and the order on Kraken follows.
- [ ] An alert reaches the phone (optionally provoked once with a wrong API key for 45 minutes).
- [ ] Extra cycles start on the expected events and not more than 3 a day.
- [ ] The panel fills in after the first cycle: market context, macro calendar, model cost.
- [ ] No unexplained errors in the add-on log for a week.

Small fixes only (bugs, wording, the panel). Anything bigger waits.

## Next: first evaluation (about 2026-12-07)

After roughly a month of live data. Questions, each with the place in the panel that answers it:

| Question | Where | What would change |
|---|---|---|
| Does the bot beat buy and hold and the trend rule after fees? | Performance, the bot vs benchmarks | If not: consider simpler rules or a smaller role for the model |
| Are the forecasts better than a coin flip? (Brier below 0.25, hit rate above 50%, calibration buckets) | Performance, forecasts | If not: lower the weight of `p_up_3d`, review the prompt |
| Do stops save money or sell too early? | Performance, stop sales | Adjust `stop_loss_pct`, the 1 ATR minimum distance, `trailing_stop_atr` |
| Do extra cycles make better calls than the daily one? | Performance, daily vs extra cycles | Tune or switch off `extra_cycles` (or only `watch_levels`) |
| Is the model cost reasonable against the capital and the result? | Model cost tile | Lower `effort`, fewer extra cycles, a cheaper model for extra cycles |
| Is `min_confidence` 0.65 blocking good calls or letting bad ones through? | Decision log, calls by confidence | Adjust the threshold |
| Is the bot too cautious while in cash? (After "hold" with no position, does the price rise more than the round-trip fees within 3-7 days, consistently and across more than one market swing?) | Performance, calls by action (hold, 3d/7d) | If yes: soften the prompt ("cautious", "hold is fine"), lower `min_confidence`, let `watch` also give a buy on a pullback to support, not only on a breakout. If no: keep it, the caution is paying |
| Does the model earn its cost in periods when the bot sits in cash and the trend rule is also out? | Model cost tile, the bot vs the trend rule | If not: skip the model call in those periods unless a `watch` level or an event fires |

Note: with three highly correlated pairs a month is still a small sample. Treat the first evaluation as a
direction, not a verdict; a firmer one after about three months.

## Later: candidates, chosen by the evaluation

Not committed - each one is picked only if the data points to it.

- **Approve trades on the phone.** The model proposes, the notification has "Buy" / "Skip". Useful if the
  calls look good but sizes or timing need a human check.
- **Limit orders instead of market orders.** Maker fee 0.25% instead of 0.40% taker, and entries closer to
  the model's level. Worth it once trades are frequent or larger.
- **Entering and exiting in steps.** Buy in two or three parts instead of one, take part of the profit early.
- **Model cost.** Prompt caching for the stable part of the request, a lower effort or a cheaper model for
  extra cycles, if the cost is out of proportion.
- **Panel.** Equity curve without deposits (the result line), calls grouped by confidence, export of the log.
- **Watchdog outside the add-on.** A Home Assistant automation that alerts when the bot's entities stop
  updating (covers the case where the add-on itself is down).
- **More pairs.** Only after the evaluation, and only uncorrelated enough to matter.

## Not planned

- Leverage, futures, margin or short selling.
- Withdrawals or any other money movement by the bot (the API key never gets those permissions).
- High-frequency trading or reacting within seconds - the model's horizon is 1-3 days.
- More data sources for their own sake: the model already sees a lot, and more input mostly adds noise.
- A "run the model now" button in the panel: the panel stays read-only, and every model call costs money.
