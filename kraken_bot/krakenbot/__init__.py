"""Kraken bot: once a day it analyses the market and news for a few pairs, asks Claude for an assessment,
and code executes the decision under fixed risk rules.

Modules, roughly in the order a cycle uses them:
  config        paths, endpoints, config.json / .env
  i18n          language selection and t(); the texts live in locales/<lang>.toml
  portfolio     state.json, equity, paper fills
  journal       the decision log (decisions.csv)
  market        public data sources (Kraken, news, futures, sentiment, CoinGecko, Deribit)
  indicators    pure calculations on candles
  claude        the prompt, the response schema and the API call
  risk          the rules in code that have the final word
  kraken        private Kraken API: live balance, entry prices, deposits, orders
  guard         stop-loss / take-profit between cycles
  evaluation    settling the model's calls, benchmark strategies, --report
  forecast      chart data: recent closes and the theoretical 3-day path from p_up_3d
  homeassistant entities, logbook and notifications
  offline       fake data for --offline
  cli           command line and the daily cycle
"""
