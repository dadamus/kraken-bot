# Security policy

## Supported versions

Only the latest release receives security fixes.

| Version | Supported |
|---|---|
| 2.x (latest) | ✅ |
| < 2.0 | ❌ |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub:
[**Report a vulnerability**](https://github.com/dadamus/kraken-bot/security/advisories/new)
(Security tab → Advisories → Report a vulnerability).

Please include what is affected (file, function, configuration), how to reproduce it, and what an
attacker could achieve. You can expect an answer within 7 days. This is a hobby project maintained
in spare time, so fixes are best effort. Once a fix is released, the advisory is published and you
are credited unless you prefer otherwise.

### In scope

Anything that could expose or misuse a user's credentials or funds, for example:

- leaking API keys (Anthropic, Kraken, the Home Assistant token) to logs, Home Assistant entities,
  notifications or third parties,
- ways for external data (news headlines, market data, API responses) to make the bot place orders
  outside its configured risk rules or limits,
- the bot trading when it should not: paper mode or `live_validate_only` sending real orders, live mode
  enabled without both switches, risk limits being bypassed,
- vulnerable dependencies or an insecure Docker image / add-on configuration.

### Out of scope

- Trading losses and the quality of the model's market assessments. The bot is an experiment and makes
  no promise of profit.
- Problems that require an attacker who already controls the machine, the Home Assistant instance or
  the user's accounts.
- Issues in Kraken, Anthropic, Home Assistant or other third-party services themselves - please report
  those to the respective vendor.

## Keeping your setup safe

- Create the Kraken API key with **Query Funds, Query Open Orders & Trades, Query Closed Orders &
  Trades and Create & Modify Orders only. Never enable Withdraw Funds.** Consider restricting the key
  to your server's IP address in Kraken's API settings.
- Use a dedicated Kraken account or sub-account that holds only the money meant for the bot.
- Keep keys in the add-on options or in `.env`. Never commit `.env`, `data/`, state or decision logs;
  they are in `.gitignore` for that reason.
- Start in paper mode, then live with `live_validate_only: true`, and only then trade for real.
- If a key may have leaked, revoke it immediately in the Kraken / Anthropic console and create a new one.
