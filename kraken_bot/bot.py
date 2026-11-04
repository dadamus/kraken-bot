#!/usr/bin/env python3
"""
Kraken crypto bot: once a day it analyses the market and news for a few pairs, asks Claude for an
assessment, and code executes the decision under fixed risk rules.

Default: PAPER TRADING (simulation, no real money).

Usage:
  python bot.py              # one cycle in paper mode
  python bot.py --offline    # test on fake data (no internet, no API keys)
  python bot.py --report     # performance summary
  python bot.py --live       # real orders (requires "mode": "live" in config.json + API keys)
  python bot.py --ha         # refresh: stop-loss / take-profit on the current price (no model) + Home Assistant
  python bot.py --ha --live  # the same with the real Kraken balance
"""
import argparse
import base64
import copy
import csv
import hashlib
import hmac
import json
import math
import os
import random
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import anthropic
import requests

BASE = Path(__file__).resolve().parent
# data directory: /config in the add-on / Docker image, ./data next to this folder when run from the repo
DATA = Path(os.environ.get("BOT_DATA_DIR") or BASE.parent / "data")
CONFIG_FILE = DATA / "config.json"
STATE_FILE = DATA / "state.json"
LOG_FILE = DATA / "decisions.csv"
LIVE_STATE_FILE = DATA / "state_live.json"  # live has its own state and log so it never mixes with paper
LIVE_LOG_FILE = DATA / "decisions_live.csv"
KRAKEN = "https://api.kraken.com"
KRAKEN_FUTURES = "https://futures.kraken.com/derivatives/api/v3/tickers"
FEAR_GREED = "https://api.alternative.me/fng/"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
DERIBIT_DVOL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
UA = {"User-Agent": "kraken-bot/2.0 (+https://github.com/dadamus/kraken-bot)"}
LOG_FIELDS = ["date", "pair", "price", "model_action", "confidence", "p_up_3d", "size_pct", "executed",
              "rule", "qty", "eur", "coin", "equity_eur", "reasoning"]
ENTITY = "sensor.kraken_bot"  # Home Assistant entity prefix


# ---------------------------------------------------------------- language
# Everything the user reads (logs, Home Assistant names, notifications, the model's reasoning) comes in
# English or Polish. Data written to the log stays machine-readable: "model: hold", "STOP", "TAKE-PROFIT"
# and "STOP-LOSS" are the same in every language.
LANGUAGES = {"en": "English", "pl": "Polish"}
LANG = "en"

MESSAGES = {
    # cycle
    "cycle_header": {"en": "== {now}  {pairs}  mode: {mode}", "pl": "== {now}  {pairs}  tryb: {mode}"},
    "price_line": {"en": "  {pair:<10} price {price:,.4f} | 1d {d1}% | 7d {d7}% | vs SMA200 {sma200}% | ATR {atr}%",
                   "pl": "  {pair:<10} cena {price:,.4f} | 1d {d1}% | 7d {d7}% | vs SMA200 {sma200}% | ATR {atr}%"},
    "vs_btc_line": {"en": "  {pair:<10} vs BTC: 7d {d7}% | 30d {d30}%", "pl": "  {pair:<10} względem BTC: 7d {d7}% | 30d {d30}%"},
    "news_count": {"en": "  news: {n}", "pl": "  newsów: {n}"},
    "offline_news": {"en": "(offline) no news", "pl": "(offline) brak newsów"},
    "market_line": {"en": "  BTC dominance {btc}% | stablecoins {stable}% | market cap 24h {cap:+}%",
                    "pl": "  Dominacja BTC {btc}% | stablecoiny {stable}% | kapitalizacja 24h {cap:+}%"},
    "dvol_line": {"en": "  {pair:<10} DVOL {dvol} (approx. ±{move}%/day), realized {realized}%",
                  "pl": "  {pair:<10} DVOL {dvol} (ok. ±{move}%/dzień), zrealizowana {realized}%"},
    "funding_line": {"en": "  {pair:<10} funding {funding:+.4f}%/8h | OI {oi} USD m ({oi_chg}% since last)",
                     "pl": "  {pair:<10} funding {funding:+.4f}%/8h | OI {oi} mln USD ({oi_chg}% od poprz.)"},
    "untracked_position": {"en": "  WARNING: {pair} holds {coin} but is not in \"pairs\" - the bot does not watch it",
                           "pl": "  UWAGA: {pair} ma pozycję {coin}, ale nie ma jej w \"pairs\" - bot jej nie pilnuje"},
    "model_summary": {"en": "  model: {summary}", "pl": "  model: {summary}"},
    "decision_line": {"en": "  {pair:<10} model: {action} ({conf:.2f}, {size:.0f}%, p_up_3d {p_up}) -> {side} - {why}",
                      "pl": "  {pair:<10} model: {action} ({conf:.2f}, {size:.0f}%, p_up_3d {p_up}) -> {side} - {why}"},
    "fill_line": {"en": "             [{mode}] {side} {qty:.8f} @ {fill:,.4f}",
                  "pl": "             [{mode}] {side} {qty:.8f} @ {fill:,.4f}"},
    "equity_line": {"en": "  equity: {equity:,.2f} EUR (start {start:,.2f})", "pl": "  kapitał: {equity:,.2f} EUR (start {start:,.2f})"},
    "balance_read_failed": {"en": "  WARNING: could not read the balance after orders ({err}), the log shows an estimate",
                            "pl": "  UWAGA: nie udało się odczytać salda po zleceniach ({err}), w dzienniku szacunek"},
    "source_skipped": {"en": "  [{name}] skipped: {err}", "pl": "  [{name}] pominięto: {err}"},
    "live_refused": {"en": "Refused: --live also requires \"mode\": \"live\" in config.json.",
                     "pl": "Odmowa: --live wymaga też \"mode\": \"live\" w config.json."},
    "mode_live_validate": {"en": "live (validate)", "pl": "live (walidacja)"},
    # decisions and rules (stored in the log, shown in HA and to the model)
    "why_hold": {"en": "model: hold", "pl": "model: hold"},
    "why_low_conf": {"en": "confidence {conf:.2f} < threshold {min}", "pl": "pewność {conf:.2f} < próg {min}"},
    "why_buy_too_small": {"en": "buy below minimum / exposure limit", "pl": "kupno poniżej minimum / limit ekspozycji"},
    "why_buy": {"en": "model signal, stop {stop:.6g} ({pct:+.1f}%)", "pl": "sygnał modelu, stop {stop:.6g} ({pct:+.1f}%)"},
    "why_nothing_to_sell": {"en": "no position to sell", "pl": "brak pozycji do sprzedaży"},
    "why_sell": {"en": "model signal", "pl": "sygnał modelu"},
    "why_stop_model": {"en": "STOP (level {level:.6g})", "pl": "STOP (poziom {level:.6g})"},
    "why_stop_set": {"en": "; stop {stop:.6g}", "pl": "; stop {stop:.6g}"},
    "why_order_failed": {"en": "{why}; order failed", "pl": "{why}; zlecenie nie przeszło"},
    "why_validate_buy": {"en": " [validation only, nothing bought]", "pl": " [tylko walidacja, nic nie kupiono]"},
    "why_validate_sell": {"en": " [validation only, nothing sold]", "pl": " [tylko walidacja, nic nie sprzedano]"},
    "halted_until": {"en": "halted until {date}", "pl": "wstrzymany do {date}"},
    "halt_drawdown": {"en": "drawdown > {dd}%, pause for {days} days", "pl": "drawdown > {dd}%, pauza {days} dni"},
    "missing_assessment": {"en": "no model assessment for this pair", "pl": "brak oceny modelu dla tej pary"},
    "offline_reasoning": {"en": "OFFLINE TEST - random signal, not analysis.", "pl": "TEST OFFLINE - losowy sygnał, nie analiza."},
    "guard_reasoning": {"en": "Protection between cycles (current price, no model).",
                        "pl": "Zabezpieczenie między cyklami (bieżąca cena, bez modelu)."},
    "guard_trigger": {"en": "  {pair:<10} {why} at price {price:,.4f} - selling the whole position",
                      "pl": "  {pair:<10} {why} przy cenie {price:,.4f} - sprzedaż całej pozycji"},
    # Claude
    "no_anthropic_key": {"en": "ANTHROPIC_API_KEY is not set", "pl": "Brak ANTHROPIC_API_KEY"},
    "claude_incomplete": {"en": "Claude did not return a complete answer (stop_reason={reason}, {details})",
                          "pl": "Claude nie zwrócił pełnej odpowiedzi (stop_reason={reason}, {details})"},
    # Kraken
    "no_kraken_keys": {"en": "KRAKEN_API_KEY / KRAKEN_API_SECRET are not set", "pl": "Brak KRAKEN_API_KEY / KRAKEN_API_SECRET"},
    "unknown_pairs": {"en": "Kraken does not know these pairs: {pairs}", "pl": "Kraken nie zna par: {pairs}"},
    "mixed_quotes": {"en": "All pairs must be quoted in the same currency (e.g. EUR)",
                     "pl": "Wszystkie pary muszą być w tej samej walucie (np. EUR)"},
    "implausible_entry": {"en": "  WARNING: {pair} entered purchase price {manual:,.2f} is implausible at the current {price:,.2f}"
                                " - ignoring it. Enter the price per 1 coin, not the total amount paid{hint}.",
                          "pl": "  UWAGA: {pair} wpisana cena zakupu {manual:,.2f} jest nierealna przy obecnej {price:,.2f}"
                                " - pomijam ją. Wpisz cenę za 1 monetę, nie kwotę zapłaconą{hint}."},
    "implausible_entry_hint": {"en": " (if you paid {manual:,.2f} EUR in total, that is approx. {per_coin:,.0f})",
                               "pl": " (jeśli zapłaciłeś {manual:,.2f} EUR łącznie, to ok. {per_coin:,.0f})"},
    "entry_line": {"en": "  {pair:<10} entry price {entry:,.2f} ({source}), position result {pnl:+.2f}%",
                   "pl": "  {pair:<10} cena wejścia {entry:,.2f} ({source}), wynik pozycji {pnl:+.2f}%"},
    "beyond_threshold": {"en": "  WARNING: {pair} is beyond the stop-loss/take-profit threshold - the bot will sell the whole position",
                         "pl": "  UWAGA: {pair} jest poza progiem stop-loss/take-profit - bot sprzeda całą pozycję"},
    "no_entry_price": {"en": "  WARNING: {pair} has no purchase price - fixed stop-loss/take-profit inactive (set it in entry_prices)",
                       "pl": "  UWAGA: {pair} brak ceny zakupu - stały stop-loss/take-profit nieaktywne (wpisz ją w entry_prices)"},
    "balance_line": {"en": "  Kraken balance: {eur:,.2f} EUR + {coins}", "pl": "  Kraken saldo: {eur:,.2f} EUR + {coins}"},
    "entry_source_history": {"en": "trade history", "pl": "historia transakcji"},
    "entry_source_manual": {"en": "manual", "pl": "ręcznie"},
    "entry_source_history+manual": {"en": "history + manual", "pl": "historia + ręcznie"},
    "volume_below_min": {"en": "  Kraken: volume {volume} below minimum {min} - skipping",
                         "pl": "  Kraken: wolumen {volume} poniżej minimum {min} - pomijam"},
    "order_rejected": {"en": "  Kraken: order rejected: {err}", "pl": "  Kraken: zlecenie odrzucone: {err}"},
    # report / evaluation
    "no_history": {"en": "No history yet (or the log is in an old format) - run the bot at least once.",
                   "pl": "Brak historii (albo dziennik w starym formacie) - uruchom bota przynajmniej raz."},
    "report_days": {"en": "Days: {days}  |  trades: {trades}", "pl": "Dni: {days}  |  transakcji: {trades}"},
    "report_bot": {"en": "Bot (from the log): {eq0:,.2f} -> {eq1:,.2f} EUR  ({pct:+.2f}%)",
                   "pl": "Bot (wg dziennika): {eq0:,.2f} -> {eq1:,.2f} EUR  ({pct:+.2f}%)"},
    "report_pair": {"en": "  {pair:<10} buys {bought}, sells {sold}", "pl": "  {pair:<10} kupna {bought}, sprzedaże {sold}"},
    "trend_rule": {"en": "close > SMA{days}", "pl": "zamknięcie > SMA{days}"},
    "ev_bot": {"en": "Bot: {ret} since start (on average {exposure}% of capital in crypto, cycles: {cycles})",
               "pl": "Bot: {ret} od startu (średnio {exposure}% kapitału w krypto, cykli: {cycles})"},
    "ev_shadow": {"en": "Since {since} (equal weight in all pairs, with fees): buy and hold {bh} | trend ({rule}) {trend}",
                  "pl": "Porównanie od {since} (równo we wszystkie pary, z opłatami): kup i trzymaj {bh} | trend ({rule}) {trend}"},
    "ev_header": {"en": "Model assessments -> average price change after 1 / 3 / 7 days:",
                  "pl": "Oceny modelu -> średnia zmiana ceny po 1 / 3 / 7 dniach:"},
    "ev_action": {"en": "  {action:<5} {n:>4} calls | {rets} | price higher after 3 days: {up} (settled {settled})",
                  "pl": "  {action:<5} {n:>4} ocen | {rets} | cena wyżej po 3 dniach: {up} (rozliczonych {settled})"},
    "ev_calibration": {"en": "p_up_3d forecast: settled {n}, direction hit rate {hit}, Brier {brier} (0.25 = coin flip, skill {skill})",
                       "pl": "Prognoza p_up_3d: rozliczonych {n}, trafność kierunku {hit}, Brier {brier} (0.25 = rzut monetą, skill {skill})"},
    "ev_bucket": {"en": "  p {range}: {n:>3} forecasts, average {avg:.2f} -> went up {up:.0f}%",
                  "pl": "  p {range}: {n:>3} prognoz, średnio {avg:.2f} -> wzrosło {up:.0f}%"},
    "ev_nothing": {"en": "p_up_3d forecast: nothing to settle yet (first results 3 days after the first forecast).",
                   "pl": "Prognoza p_up_3d: jeszcze nic do rozliczenia (pierwsze wyniki 3 dni po pierwszej prognozie)."},
    "ev_noise": {"en": "Note: fewer than {n} settled forecasts and the pairs are highly correlated - mostly noise so far.",
                 "pl": "Uwaga: mniej niż {n} rozliczonych prognoz, a pary są mocno skorelowane - na razie to głównie szum."},
    # Home Assistant
    "ha_missing": {"en": "  [HA] skipped: home_assistant.url in config.json or HA_TOKEN is missing",
                   "pl": "  [HA] pominięto: brak home_assistant.url w config.json albo HA_TOKEN"},
    "ha_updated": {"en": "  [HA] updated ({url})", "pl": "  [HA] zaktualizowano ({url})"},
    "ha_error": {"en": "  [HA] error: {err}", "pl": "  [HA] błąd: {err}"},
    "ha_equity": {"en": "Kraken bot equity", "pl": "Kraken bot kapitał"},
    "ha_return": {"en": "Kraken bot return", "pl": "Kraken bot wynik"},
    "ha_free_eur": {"en": "Kraken bot free EUR", "pl": "Kraken bot wolne EUR"},
    "ha_decision": {"en": "Kraken bot last decision", "pl": "Kraken bot ostatnia decyzja"},
    "ha_performance": {"en": "Kraken bot 3-day hit rate", "pl": "Kraken bot trafność 3d"},
    "ha_no_decision": {"en": "none", "pl": "brak"},
    "ha_notify_message": {"en": "{qty} @ {price} ({rule}). Equity {equity:,.2f} EUR",
                          "pl": "{qty} @ {price} ({rule}). Kapitał {equity:,.2f} EUR"},
}


def set_language(cfg=None):
    """Language from the BOT_LANGUAGE variable (add-on option), then config.json "language", default English."""
    global LANG
    lang = (os.environ.get("BOT_LANGUAGE") or (cfg or {}).get("language") or "en").lower()
    LANG = lang if lang in LANGUAGES else "en"
    return LANG


def t(key, **kw):
    entry = MESSAGES[key]
    return entry.get(LANG, entry["en"]).format(**kw)


# ---------------------------------------------------------------- config / env
def load_env():
    env = DATA / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config():
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if "pairs" not in cfg:  # old format: a single pair
        cfg["pairs"] = [cfg["pair"]]
    return cfg


def empty_position():
    return {"coin": 0.0, "entry_price": None}


def use_live_files():
    global STATE_FILE, LOG_FILE
    STATE_FILE, LOG_FILE = LIVE_STATE_FILE, LIVE_LOG_FILE


def mode_label(cfg, live):
    if not live:
        return "paper"
    return t("mode_live_validate") if cfg.get("live_validate_only", True) else "live"


def load_state(cfg):
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    else:
        state = {"eur": cfg["paper_start_eur"], "start_eur": cfg["paper_start_eur"],
                 "positions": {}, "first_prices": {}, "halted_until": None}
    if "positions" not in state:  # migration from the single-pair state
        old_pair = cfg.get("pair", cfg["pairs"][0])
        state["positions"] = {old_pair: {"coin": state.pop("coin", 0.0),
                                         "entry_price": state.pop("entry_price", None)}}
        fp = state.pop("first_price", None)
        state["first_prices"] = {old_pair: fp} if fp else {}
    for pair in cfg["pairs"]:
        state["positions"].setdefault(pair, empty_position())
    return state


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def equity_of(state, prices):
    return state["eur"] + sum(p["coin"] * prices[pair] for pair, p in state["positions"].items()
                              if pair in prices)


# ---------------------------------------------------------------- market data
def fetch_ohlc(pair, days=60):
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    r = requests.get(f"{KRAKEN}/0/public/OHLC",
                     params={"pair": pair, "interval": 1440, "since": since},
                     headers=UA, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"Kraken: {data['error']}")
    key = next(k for k in data["result"] if k != "last")
    rows = data["result"][key]
    # [time, open, high, low, close, vwap, volume, count]; the last candle is today's, still open
    candles = [{"t": int(c[0]), "o": float(c[1]), "h": float(c[2]), "l": float(c[3]),
                "c": float(c[4]), "v": float(c[6])} for c in rows]
    return candles[:-1], candles[-1]["c"]  # (closed days, current price)


def indicators(closed, price):
    closes = [c["c"] for c in closed]

    def chg(n):
        return (closes[-1] / closes[-1 - n] - 1) * 100 if len(closes) > n else None

    def sma(n):
        return sum(closes[-n:]) / n if len(closes) >= n else None

    def sig(x):  # 6 significant digits - enough for BTC and for cheap coins
        return float(f"{x:.6g}")

    def rsi(n=14):  # Wilder's RSI
        if len(closes) <= n:
            return None
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gain = sum(max(d, 0) for d in deltas[:n]) / n
        loss = sum(max(-d, 0) for d in deltas[:n]) / n
        for d in deltas[n:]:
            gain = (gain * (n - 1) + max(d, 0)) / n
            loss = (loss * (n - 1) + max(-d, 0)) / n
        return 100.0 if loss == 0 else round(100 - 100 / (1 + gain / loss), 1)

    def atr(n=14):  # Wilder's ATR - typical daily range, used for stop placement
        if len(closed) <= n:
            return None
        trs = [max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"]))
               for p, c in zip(closed, closed[1:])]
        a = sum(trs[:n]) / n
        for tr in trs[n:]:
            a = (a * (n - 1) + tr) / n
        return a

    def pct_vs(x):
        return round((price / x - 1) * 100, 2) if x else None

    def weekly(n=26):  # complete weeks (Mon-Sun), oldest first
        weeks = {}
        for c in closed:
            d = datetime.fromtimestamp(c["t"], timezone.utc)
            weeks.setdefault((d - timedelta(days=d.weekday())).strftime("%Y-%m-%d"), []).append(c)
        rows = [[k, sig(w[-1]["c"]), sig(max(x["h"] for x in w)), sig(min(x["l"] for x in w))]
                for k, w in sorted(weeks.items()) if len(w) == 7]
        return {"columns": ["week_start", "close", "high", "low"], "rows": rows[-n:]}

    sma200_20d_ago = sum(closes[-220:-20]) / 200 if len(closes) >= 220 else None
    a14 = atr()
    last365 = closed[-365:]
    hi365, lo365 = max(c["h"] for c in last365), min(c["l"] for c in last365)
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))][-30:]
    vol = (sum(r * r for r in rets) / len(rets)) ** 0.5 * 100 if rets else None
    y = closed[-1]
    avg_vol = sum(c["v"] for c in closed[-31:-1]) / max(1, len(closed[-31:-1]))
    last30 = closed[-30:]
    hi30, lo30 = max(c["h"] for c in last30), min(c["l"] for c in last30)
    vol30 = sum(c["v"] for c in last30) / len(last30)
    return {
        "price_now": round(price, 6),
        "yesterday": {"open": y["o"], "high": y["h"], "low": y["l"], "close": y["c"],
                      "volume_vs_30d_avg": round(y["v"] / avg_vol, 2) if avg_vol else None},
        "change_1d_pct": round(chg(1), 2) if chg(1) is not None else None,
        "change_7d_pct": round(chg(7), 2) if chg(7) is not None else None,
        "change_30d_pct": round(chg(30), 2) if chg(30) is not None else None,
        "change_90d_pct": round(chg(90), 2) if chg(90) is not None else None,
        "change_365d_pct": round(chg(365), 2) if chg(365) is not None else None,
        "sma7": round(sma(7), 6) if sma(7) else None,
        "sma30": round(sma(30), 6) if sma(30) else None,
        "sma50": sig(sma(50)) if sma(50) else None,
        "sma200": sig(sma(200)) if sma(200) else None,
        "pct_vs_sma50": pct_vs(sma(50)),
        "pct_vs_sma200": pct_vs(sma(200)),
        "sma200_change_20d_pct": round((sma(200) / sma200_20d_ago - 1) * 100, 2) if sma200_20d_ago else None,
        "atr14": sig(a14) if a14 else None,
        "atr14_pct": round(a14 / price * 100, 2) if a14 else None,
        "daily_volatility_pct": round(vol, 2) if vol else None,
        "rsi14": rsi(),
        "range_30d": {"high": sig(hi30), "low": sig(lo30),
                      "pct_below_high": round((1 - price / hi30) * 100, 2),
                      "pct_above_low": round((price / lo30 - 1) * 100, 2)},
        "range_365d": {"days": len(last365), "high": sig(hi365), "low": sig(lo365),
                       "pct_below_high": round((1 - price / hi365) * 100, 2),
                       "pct_above_low": round((price / lo365 - 1) * 100, 2)},
        "last_26_weeks": weekly(),
        "last_30_days": {  # oldest first; volume_rel = volume / average of these 30 days
            "columns": ["date", "close", "high", "low", "volume_rel"],
            "rows": [[datetime.fromtimestamp(c["t"], timezone.utc).strftime("%m-%d"), sig(c["c"]),
                      sig(c["h"]), sig(c["l"]), round(c["v"] / vol30, 2)] for c in last30]},
    }


def fetch_news(feeds, hours=30, limit=40):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    items = []
    for url in feeds:
        try:
            r = requests.get(url, headers=UA, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                pub = it.findtext("pubDate")
                try:
                    dt = parsedate_to_datetime(pub) if pub else None
                except Exception:
                    dt = None
                if title and (dt is None or dt >= cutoff):
                    items.append(title)
        except Exception as e:
            print(t("source_skipped", name=f"news {url}", err=e))
    seen, out = set(), []
    for title in items:
        if title.lower() not in seen:
            seen.add(title.lower())
            out.append(title)
    return out[:limit]


def fetch_derivatives(pairs, state):
    """Funding and open interest of perpetual contracts from Kraken Futures (USD market, same sentiment)."""
    r = requests.get(KRAKEN_FUTURES, headers=UA, timeout=20)
    r.raise_for_status()
    tickers = {x["symbol"]: x for x in r.json()["tickers"]}
    prev = state.setdefault("open_interest", {})
    out = {}
    for pair in pairs:
        x = tickers.get(f"PF_{pair[:-3]}USD")  # XBTEUR -> PF_XBTUSD
        if not x:
            continue
        old = prev.get(pair)
        out[pair] = {  # Kraken reports hourly funding; converted to the customary % per 8h
            "funding_8h_pct": round(x["relativeFundingRate"] * 8 * 100, 4),
            "funding_next_8h_pct": round(x["relativeFundingRatePrediction"] * 8 * 100, 4),
            "open_interest_usd_m": round(x["openInterest"] * x["markPrice"] / 1e6, 1),
            "open_interest_change_pct": round((x["openInterest"] / old["coins"] - 1) * 100, 2) if old else None,
            "open_interest_change_since": old["date"] if old else None,
            "perp_premium_pct": round((x["markPrice"] / x["indexPrice"] - 1) * 100, 3),
        }
        prev[pair] = {"coins": x["openInterest"], "date": datetime.now().strftime("%Y-%m-%d %H:%M")}
    return out


def fetch_fear_greed(days=7):
    r = requests.get(FEAR_GREED, params={"limit": days}, headers=UA, timeout=15)
    r.raise_for_status()
    data = r.json()["data"]
    return {"today": int(data[0]["value"]), "label": data[0]["value_classification"],
            "last_days_newest_first": [int(d["value"]) for d in data]}


def relative_strength(closed_by_pair, prices, base="XBTEUR"):
    """Each pair priced in BTC (e.g. ETH/BTC) from our EUR candles: is the coin stronger than the market?"""
    if base not in closed_by_pair:
        return {}
    btc = {c["t"]: c["c"] for c in closed_by_pair[base]}
    out = {}
    for pair, cs in closed_by_pair.items():
        if pair == base:
            continue
        ratio = [c["c"] / btc[c["t"]] for c in cs if c["t"] in btc]
        now = prices[pair] / prices[base]

        def chg(n):
            return round((now / ratio[-n] - 1) * 100, 2) if len(ratio) >= n else None

        sma50 = sum(ratio[-50:]) / 50 if len(ratio) >= 50 else None
        out[pair] = {"ratio_now": float(f"{now:.6g}"), "change_7d_pct": chg(7), "change_30d_pct": chg(30),
                     "change_90d_pct": chg(90),
                     "pct_vs_sma50": round((now / sma50 - 1) * 100, 2) if sma50 else None}
    return out


def fetch_market_overview(state):
    """The whole crypto market from CoinGecko: BTC/ETH dominance, stablecoin share (rising share = flight
    to cash), market cap change. Changes are measured against the previous cycle."""
    r = requests.get(COINGECKO_GLOBAL, headers=UA, timeout=15)
    r.raise_for_status()
    d = r.json()["data"]
    share = d["market_cap_percentage"]
    now = {"btc_dominance_pct": round(share.get("btc", 0), 2),
           "eth_dominance_pct": round(share.get("eth", 0), 2),
           "stablecoin_share_pct": round(share.get("usdt", 0) + share.get("usdc", 0), 2),
           "total_market_cap_usd_bn": round(d["total_market_cap"]["usd"] / 1e9, 1)}
    prev = state.get("market_overview")
    out = {**now, "total_market_cap_change_24h_pct": round(d.get("market_cap_change_percentage_24h_usd", 0), 2)}
    if prev:
        out["change_since"] = prev["date"]
        out["changes_since_last_cycle"] = {k: round(now[k] - prev[k], 2) for k in now if k in prev}
    state["market_overview"] = {**now, "date": datetime.now().strftime("%Y-%m-%d %H:%M")}
    return out


def fetch_implied_vol(pairs, closed_by_pair, days=90):
    """Deribit DVOL index: annualised implied volatility from options (BTC, ETH) - how big a move the
    market prices in. Compared with the realised volatility of the last 30 days."""
    now = int(time.time() * 1000)
    out = {}
    for pair in pairs:
        cur = {"XBT": "BTC"}.get(pair[:-3], pair[:-3])
        r = requests.get(DERIBIT_DVOL, headers=UA, timeout=15,
                         params={"currency": cur, "resolution": "1D", "end_timestamp": now,
                                 "start_timestamp": now - days * 86400 * 1000})
        r.raise_for_status()
        rows = r.json()["result"]["data"]
        if not rows:  # Deribit has no DVOL for this coin (e.g. SOL)
            continue
        closes = [x[4] for x in rows]
        dvol = closes[-1]
        cl = [c["c"] for c in closed_by_pair.get(pair, [])][-31:]
        rets = [math.log(b / a) for a, b in zip(cl, cl[1:])]
        realized = (sum(x * x for x in rets) / len(rets)) ** 0.5 * math.sqrt(365) * 100 if rets else None
        out[pair] = {"dvol_now": dvol,
                     "implied_daily_move_pct": round(dvol / math.sqrt(365), 2),
                     "dvol_7d_ago": closes[-8] if len(closes) >= 8 else None,
                     f"dvol_{days}d_min": min(closes), f"dvol_{days}d_max": max(closes),
                     "realized_vol_30d_annual_pct": round(realized, 1) if realized else None}
    return out


def recent_decisions(pairs, prices, days=7):
    """The model's recent calls and what the price did afterwards, so it can see whether it was right.
    Also automatic stop / take-profit sales between cycles (action "stop") from the same period."""
    rows = read_log()
    dates = sorted({r["date"] for r in rows if r["model_action"] != "stop"})[-days:]
    since, keep = (dates[0] if dates else ""), set(dates)

    def row(r):
        out = {"date": r["date"], "action": r["model_action"], "confidence": float(r["confidence"] or 0),
               "p_up_3d": float(r["p_up_3d"]) if r.get("p_up_3d") else None,
               "executed": r["executed"], "price_then": float(r["price"]),
               "price_change_since_pct": round((prices[pair] / float(r["price"]) - 1) * 100, 2)}
        if r["model_action"] == "stop":
            out["rule"] = r["rule"]  # e.g. "STOP (level 67900)" or "TAKE-PROFIT"
        return out

    result = {}
    for pair in pairs:
        result[pair] = [row(r) for r in rows if r["pair"] == pair and
                        (r["date"] in keep or (r["model_action"] == "stop" and r["date"] >= since))]
    return result


def optional(name, fn, *args):
    """Extra data: a failing source does not stop the cycle, the model simply gets less information."""
    try:
        return fn(*args)
    except Exception as e:
        print(t("source_skipped", name=name, err=e))
        return None


# ---------------------------------------------------------------- Claude
SYSTEM_PROMPT = """You are a cautious crypto market analyst inside an experimental trading bot.
You receive the previous day's data and indicators for several pairs, the portfolio state,
risk limits and news headlines. For EACH pair assess the short-term (1-3 day) direction.
Be skeptical: most days are noise, and "hold" is fine. Remember that crypto assets are highly
correlated, so buying several at once is in practice one bigger bet.
Exposure limits (buy_limits) only restrict NEW purchases and are enforced by code. An existing position
above a limit is NOT a reason to sell: give "sell" only when you expect the price to fall.
Do not invent facts beyond the data provided.
Additional context (may be missing, in which case the data is unavailable):
- derivatives: perpetual funding (% per 8h; high positive = crowded leveraged longs),
  open interest change since the previous cycle, perpetual premium to the index,
- fear_greed: sentiment index 0-100 (0 = extreme fear, 100 = extreme greed),
- own_recent_decisions: your calls from recent days and the price change since; entries with action "stop"
  are automatic sales of the whole position between cycles (rule: STOP = price broke the stop,
  TAKE-PROFIT = target reached). After a stop do not buy back right away without a new reason - the price
  has just broken the level you considered the invalidation of your thesis,
- own_track_record: settlement of all your earlier calls - average price change 1/3/7 days after
  buy/hold/sell and calibration of p_up_3d (Brier: 0.25 = coin flip, lower = better; buckets: whether e.g.
  ~0.7 forecasts came true ~70% of the time). With a few dozen samples this is still mostly noise,
- market_overview: the whole crypto market (BTC/ETH dominance, stablecoin share - rising usually means a
  flight to cash, market cap change; changes_since_last_cycle in percentage points),
- implied_vol: Deribit options DVOL (BTC and ETH only) - the move priced in by the options market, compared
  with realised volatility; implied far above realised = the market expects a move.
In markets every pair also has:
- short term: rsi14, range_30d, last_30_days (daily candles, oldest first) - look at the path,
  support and resistance, not just single percentage changes,
- the bigger picture: sma50/sma200 and the distance to them, the SMA200 slope, range_365d, last_26_weeks
  (weekly candles) - a short-term buy against the weekly trend needs a stronger reason,
- atr14 / atr14_pct: the typical daily range (for stop placement),
- vs_btc (pairs other than BTC): the price in BTC - is the coin stronger or weaker than the market.
Treat this as context, not a standalone signal: extreme funding and fast-rising open interest
mostly mean the risk of a sharp move the other way, and extreme sentiment can be contrarian.
Do not change your mind versus your recent calls without a new reason; if you were wrong, take it into account.
Write "reasoning" and "summary" in {LANGUAGE}.
Respond with JSON in this format (one decision per pair):
{"decisions": [
   {"pair": "<pair name from the data>",
    "action": "buy" | "sell" | "hold",
    "confidence": number 0-1,
    "p_up_3d": number 0-1 - probability that in 3 days (72 h) the price will be higher than price_now;
               give it for EVERY pair, also with hold; 0.5 = no view. These forecasts are scored,
               so do not overstate your confidence - they typically fall within 0.35-0.65,
    "size_pct": number 0-100 (buy: % of free EUR, sell: % of the position held),
    "stop_price": price in EUR at which your thesis no longer holds (e.g. below support), or null.
                  With buy: the stop of the new position. Code keeps it at least 1 ATR below the price and
                  no further than stop_loss_pct; without it 2 ATR is used. With hold and an open position you
                  may raise the stop (e.g. below a new support), it cannot be lowered; a position without a
                  stop (stop_price null in portfolio.positions) gets the one you give. Current stops are in
                  portfolio.positions,
    "reasoning": "1-3 sentences"}
 ],
 "summary": "1-2 sentences about the whole market"}"""


def system_prompt():
    return SYSTEM_PROMPT.replace("{LANGUAGE}", LANGUAGES[LANG])


def parse_signal(d):
    action = d.get("action", "hold")
    if action not in ("buy", "sell", "hold"):
        action = "hold"
    try:
        p_up = round(max(0.0, min(1.0, float(d["p_up_3d"]))), 3)
    except (KeyError, TypeError, ValueError):
        p_up = None
    try:
        stop = float(d["stop_price"]) if d.get("stop_price") else None
    except (TypeError, ValueError):
        stop = None
    return {"action": action, "stop_price": stop,
            "confidence": max(0.0, min(1.0, float(d.get("confidence", 0)))),
            "p_up_3d": p_up,
            "size_pct": max(0.0, min(100.0, float(d.get("size_pct", 0)))),
            "reasoning": str(d.get("reasoning", ""))[:600]}


def decision_schema(pairs):
    num, nullable = {"type": "number"}, {"anyOf": [{"type": "number"}, {"type": "null"}]}
    decision = {"type": "object", "additionalProperties": False,
                "required": ["pair", "action", "confidence", "p_up_3d", "size_pct", "stop_price", "reasoning"],
                "properties": {"pair": {"type": "string", "enum": list(pairs)},
                               "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
                               "confidence": num, "p_up_3d": num, "size_pct": num,
                               "stop_price": nullable, "reasoning": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False, "required": ["decisions", "summary"],
            "properties": {"decisions": {"type": "array", "items": decision},
                           "summary": {"type": "string"}}}


def ask_claude(cfg, markets, news, portfolio, context):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(t("no_anthropic_key"))
    r_cfg = cfg["risk"]
    user = json.dumps({"markets": markets, "portfolio": portfolio,
                       "buy_limits": {"max_total_exposure_pct": r_cfg["max_exposure_pct"],
                                      "max_exposure_per_pair_pct": r_cfg.get("max_exposure_per_pair_pct",
                                                                             r_cfg["max_exposure_pct"]),
                                      "max_trade_eur": r_cfg["max_trade_eur"],
                                      "stop_loss_pct": r_cfg["stop_loss_pct"],
                                      "min_confidence": r_cfg["min_confidence"]},
                       "news_last_24h": news, **context},
                      ensure_ascii=False, indent=1)
    # Structured outputs: the API guarantees valid JSON matching the schema. fallbacks="default": if a
    # safety classifier declines the request, the API re-runs it on the recommended fallback model.
    client = anthropic.Anthropic(api_key=key, timeout=300, max_retries=3)
    resp = client.beta.messages.create(
        model=cfg["model"], max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": cfg.get("effort", "high"),
                       "format": {"type": "json_schema", "schema": decision_schema(cfg["pairs"])}},
        system=system_prompt(), messages=[{"role": "user", "content": user}])
    if resp.stop_reason != "end_turn":
        raise RuntimeError(t("claude_incomplete", reason=resp.stop_reason,
                             details=getattr(resp, "stop_details", None)))
    d = json.loads(next(b.text for b in resp.content if b.type == "text"))
    by_pair = {str(x.get("pair", "")).upper(): x for x in d.get("decisions", [])}
    signals = {}
    for pair in cfg["pairs"]:
        if pair in by_pair:
            signals[pair] = parse_signal(by_pair[pair])
        else:  # the model skipped the pair -> do nothing
            signals[pair] = {"action": "hold", "confidence": 0.0, "p_up_3d": None, "stop_price": None,
                             "size_pct": 0.0, "reasoning": t("missing_assessment")}
    return signals, str(d.get("summary", ""))[:600]


# ---------------------------------------------------------------- risk rules
def check_halt(cfg, state, prices):
    """Halts the whole portfolio after a large drop in equity. Returns the reason or None."""
    r = cfg["risk"]
    now = datetime.now(timezone.utc)
    if state.get("halted_until") and now.isoformat() < state["halted_until"]:
        return t("halted_until", date=state["halted_until"][:10])
    if equity_of(state, prices) < state["start_eur"] * (1 - r["max_drawdown_pct"] / 100):
        state["halted_until"] = (now + timedelta(days=r["halt_days"])).isoformat()
        return t("halt_drawdown", dd=r["max_drawdown_pct"], days=r["halt_days"])
    return None


def stop_level(cfg, price, atr, model_stop):
    """Stop for a buy: the model's invalidation level (default 2 ATR below the price), but no closer than
    1 ATR (so ordinary noise does not shake us out) and no further than stop_loss_pct."""
    floor = price * (1 - cfg["risk"]["stop_loss_pct"] / 100)
    if not atr:
        return round(model_stop, 8) if model_stop and floor <= model_stop < price else floor
    stop = model_stop if model_stop and model_stop < price else price - 2 * atr
    return round(max(floor, min(stop, price - atr)), 8)


def tighten_stop(cfg, pos, signal, price, atr=None):
    """With an open position the model may raise the stop (never lower it). A position without a stop
    (coins bought outside the bot) gets its first stop within the same limits as a buy.
    Returns the new level or None."""
    new = signal.get("stop_price")
    if pos["coin"] <= 0 or not new or new >= price:
        return None
    if not pos.get("stop_price"):
        pos["stop_price"] = stop_level(cfg, price, atr, new)
        return pos["stop_price"]
    if pos["stop_price"] < new:
        pos["stop_price"] = round(new, 8)
        return pos["stop_price"]
    return None


def protective_exit(cfg, pos, price):
    """Stop-loss / take-profit: always active, regardless of the model and of a halt."""
    r = cfg["risk"]
    if pos["coin"] > 0 and pos.get("stop_price") and price <= pos["stop_price"]:
        return {"side": "sell", "coin": pos["coin"], "why": t("why_stop_model", level=pos["stop_price"])}
    if pos["coin"] > 0 and pos["entry_price"]:
        if price <= pos["entry_price"] * (1 - r["stop_loss_pct"] / 100):
            return {"side": "sell", "coin": pos["coin"], "why": "STOP-LOSS"}
        if r.get("take_profit_pct") and price >= pos["entry_price"] * (1 + r["take_profit_pct"] / 100):
            return {"side": "sell", "coin": pos["coin"], "why": "TAKE-PROFIT"}
    return None


def guard(cfg, state, prices, live, pair_info=None):
    """Protection on every refresh (--ha, in the add-on every refresh_minutes): stop-loss and take-profit
    on the current price. No model - just API prices. Returns rows for the log."""
    if not cfg["risk"].get("intraday_stops", True):
        return []
    validate = live and cfg.get("live_validate_only", True)
    today = datetime.now().strftime("%Y-%m-%d")
    fired = state.setdefault("guard_fired", {})
    book = copy.deepcopy(state) if live else state  # in live the real balance is read at the end anyway
    rows, now_s = [], datetime.now().strftime("%Y-%m-%d %H:%M")
    for pair, price in prices.items():
        pos = book["positions"].get(pair)
        order = protective_exit(cfg, pos, price) if pos else None
        # in validation mode the position stays, so without this the same stop would fire every 15 minutes
        if not order or (validate and fired.get(pair) == today):
            continue
        print(t("guard_trigger", pair=pair, why=order["why"], price=price))
        qty = 0.0
        if live and not live_execute(cfg, pair_info[pair], order, price):
            order = {"side": "error", "why": t("why_order_failed", why=order["why"])}
        else:
            if validate:
                order["why"] += t("why_validate_sell")
            _, qty = paper_execute(cfg, book, pair, order, price)
            fired[pair] = today
        rows.append({"date": now_s, "pair": pair, "price": round(price, 6), "model_action": "stop",
                     "confidence": 0.0, "p_up_3d": None, "size_pct": 100, "executed": order["side"],
                     "rule": order["why"], "qty": round(qty, 8), "eur": 0,
                     "coin": round(book["positions"][pair]["coin"], 8), "equity_eur": 0,
                     "reasoning": t("guard_reasoning")})
    if rows and live:
        try:
            time.sleep(2)  # give market orders a moment to settle
            sync_live(cfg, state, prices)
            book = state
        except Exception as e:
            print(t("balance_read_failed", err=e))
    for row in rows:
        row["eur"], row["equity_eur"] = round(book["eur"], 2), round(equity_of(book, prices), 2)
    return rows


def decide(cfg, state, pair, prices, signal, atr=None):
    """Turns the model's signal for one pair into an order. The rules in code have the final word."""
    r = cfg["risk"]
    price = prices[pair]
    pos = state["positions"][pair]

    if signal["action"] == "hold":
        return {"side": "none", "why": t("why_hold")}
    if signal["confidence"] < r["min_confidence"]:
        return {"side": "none", "why": t("why_low_conf", conf=signal["confidence"], min=r["min_confidence"])}

    if signal["action"] == "buy":
        equity = equity_of(state, prices)
        total_exposure = equity - state["eur"]
        room_total = equity * r["max_exposure_pct"] / 100 - total_exposure
        per_pair_pct = r.get("max_exposure_per_pair_pct", r["max_exposure_pct"])
        room_pair = equity * per_pair_pct / 100 - pos["coin"] * price
        eur = min(state["eur"] * signal["size_pct"] / 100, r["max_trade_eur"],
                  room_total, room_pair, state["eur"])
        if eur < r["min_trade_eur"]:
            return {"side": "none", "why": t("why_buy_too_small")}
        stop = stop_level(cfg, price, atr, signal.get("stop_price"))
        return {"side": "buy", "eur": round(eur, 2), "stop_price": stop,
                "why": t("why_buy", stop=stop, pct=(stop / price - 1) * 100)}

    coin = pos["coin"] * signal["size_pct"] / 100
    if coin * price < r["min_trade_eur"]:
        return {"side": "none", "why": t("why_nothing_to_sell")}
    return {"side": "sell", "coin": coin, "why": t("why_sell")}


# ---------------------------------------------------------------- execution
def paper_execute(cfg, state, pair, order, price):
    fee = cfg["fee_pct"] / 100
    slip = cfg.get("slippage_pct", 0.1) / 100
    pos = state["positions"][pair]
    if order["side"] == "buy":
        fill = price * (1 + slip)
        coin = order["eur"] * (1 - fee) / fill
        old_cost = (pos["entry_price"] or 0) * pos["coin"]
        state["eur"] -= order["eur"]
        pos["coin"] += coin
        pos["entry_price"] = (old_cost + order["eur"]) / pos["coin"]
        if order.get("stop_price"):  # adding to a position: the stop of the latest thesis applies
            pos["stop_price"] = order["stop_price"]
        return fill, coin
    if order["side"] == "sell":
        fill = price * (1 - slip)
        coin = min(order["coin"], pos["coin"])
        state["eur"] += coin * fill * (1 - fee)
        pos["coin"] -= coin
        if pos["coin"] < 1e-9:
            pos["coin"], pos["entry_price"], pos["stop_price"] = 0.0, None, None
        return fill, coin
    return None, 0.0


def kraken_private(path, data):
    key, secret = os.environ.get("KRAKEN_API_KEY"), os.environ.get("KRAKEN_API_SECRET")
    if not key or not secret:
        raise RuntimeError(t("no_kraken_keys"))
    data = dict(data, nonce=str(int(time.time() * 1000)))
    post = urllib.parse.urlencode(data)
    sha = hashlib.sha256((data["nonce"] + post).encode()).digest()
    sig = hmac.new(base64.b64decode(secret), path.encode() + sha, hashlib.sha512)
    r = requests.post(KRAKEN + path, data=data, timeout=20,
                      headers={**UA, "API-Key": key, "API-Sign": base64.b64encode(sig.digest()).decode()})
    r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError(f"Kraken: {j['error']}")
    return j["result"]


def kraken_pairs(pairs):
    """Pair data from Kraken: internal name, asset codes (XBTEUR -> XXBT / ZEUR), precision, minimum."""
    r = requests.get(f"{KRAKEN}/0/public/AssetPairs", params={"pair": ",".join(pairs)},
                     headers=UA, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"Kraken: {data['error']}")
    info = {v["altname"]: {"key": k, "base": v["base"], "quote": v["quote"],
                           "lot_decimals": int(v["lot_decimals"]), "ordermin": float(v["ordermin"])}
            for k, v in data["result"].items() if v["altname"] in pairs}
    if set(pairs) - set(info):
        raise RuntimeError(t("unknown_pairs", pairs=sorted(set(pairs) - set(info))))
    if len({i["quote"] for i in info.values()}) != 1:
        raise RuntimeError(t("mixed_quotes"))
    return info


def kraken_trades(max_pages=10):
    trades, ofs = [], 0
    for _ in range(max_pages):  # Kraken returns 50 trades per page
        res = kraken_private("/0/private/TradesHistory", {"ofs": ofs})
        page = list(res["trades"].values())
        trades += page
        ofs += len(page)
        if not page or ofs >= int(res["count"]):
            break
    return sorted(trades, key=lambda x: float(x["time"]))


def manual_entry_prices(cfg):
    """Purchase prices entered by hand (config.json "entry_prices" or the add-on option) - for coins
    bought outside the trade history, e.g. via "Buy crypto" in the app or deposited from elsewhere."""
    prices = {**cfg.get("entry_prices", {}), **json.loads(os.environ.get("BOT_ENTRY_PRICES") or "{}")}
    return {k.upper(): float(v) for k, v in prices.items() if v}


def position_from_trades(trades, info, pair, coin, manual_price, min_coin):
    """Average cost of the current position. Coins outside the trade history (bought in the app,
    deposited) are treated as bought first at the manually entered price. Buys change the average,
    sells do not. Returns (entry price, source) or (None, None); source is "history", "manual"
    or "history+manual"."""
    own = [x for x in trades if x["pair"] in (info["key"], pair)]
    net = sum(float(x["vol"]) * (1 if x["type"] == "buy" else -1) for x in own)
    outside = coin - net
    qty = outside if manual_price and outside >= min_coin else 0.0
    cost = qty * (manual_price or 0)
    for x in own:
        vol = float(x["vol"])
        if x["type"] == "buy":
            qty += vol
            cost += float(x["cost"]) + float(x["fee"])
        elif qty > 0:
            sold = min(vol, qty)
            cost *= 1 - sold / qty
            qty -= sold
    if qty < 1e-12:
        return None, None
    if outside >= min_coin and manual_price:
        return cost / qty, "history+manual" if own else "manual"
    return cost / qty, "history"


def sync_live(cfg, state, prices):
    """Overwrites free EUR and positions with the real Kraken balance. Returns pair data for orders."""
    info = kraken_pairs(cfg["pairs"])
    bal = {k: float(v) for k, v in kraken_private("/0/private/Balance", {}).items()}
    trades = kraken_trades()
    manual = manual_entry_prices(cfg)
    r = cfg["risk"]
    state["eur"] = bal.get(next(iter(info.values()))["quote"], 0.0)
    for pair, i in info.items():
        coin = bal.get(i["base"], 0.0)
        entry = source = None
        min_coin = r["min_trade_eur"] / prices[pair]  # dust left after a sale is ignored
        manual_price = manual.get(pair)
        if manual_price and not prices[pair] / 3 <= manual_price <= prices[pair] * 3:
            # the usual mistake: the total amount paid entered instead of the price per coin
            hint = t("implausible_entry_hint", manual=manual_price, per_coin=manual_price / coin) if coin else ""
            print(t("implausible_entry", pair=pair, manual=manual_price, price=prices[pair], hint=hint))
            manual_price = None
        if coin >= min_coin:
            entry, source = position_from_trades(trades, i, pair, coin, manual_price, min_coin)
        if entry:
            pnl = (prices[pair] / entry - 1) * 100
            print(t("entry_line", pair=pair, entry=entry, source=t(f"entry_source_{source}"), pnl=pnl))
            if pnl <= -r["stop_loss_pct"] or (r.get("take_profit_pct") and pnl >= r["take_profit_pct"]):
                print(t("beyond_threshold", pair=pair))
        elif coin * prices[pair] >= r["min_trade_eur"]:
            print(t("no_entry_price", pair=pair))
        old_stop = state["positions"].get(pair, {}).get("stop_price")  # Kraken knows nothing about our stop
        state["positions"][pair] = {"coin": coin, "entry_price": entry, "entry_source": source,
                                    "stop_price": old_stop if coin >= min_coin else None}
    if not state.get("synced_at"):  # first live start: the reference point for return and drawdown
        state["start_eur"] = round(equity_of(state, prices), 2)
    state["synced_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(t("balance_line", eur=state["eur"],
            coins=", ".join(f"{p} {state['positions'][p]['coin']:.8f}" for p in info)))
    return info


def live_execute(cfg, info, order, price):
    """Market order on Kraken. Returns False when the order did not go through."""
    if order["side"] == "buy":  # reserve for fee and slippage so EUR does not run short
        reserve = (cfg["fee_pct"] + cfg.get("slippage_pct", 0.1)) / 100
        volume = order["eur"] * (1 - reserve) / price
    else:
        volume = order["coin"]
    dec = info["lot_decimals"]
    volume = math.floor(volume * 10 ** dec) / 10 ** dec
    if volume < info["ordermin"]:
        print(t("volume_below_min", volume=volume, min=info["ordermin"]))
        return False
    params = {"pair": info["key"], "type": order["side"], "ordertype": "market",
              "volume": f"{volume:.{dec}f}"}
    if cfg.get("live_validate_only", True):
        params["validate"] = "true"  # Kraken checks the order but does NOT place it
    try:
        res = kraken_private("/0/private/AddOrder", params)
    except Exception as e:
        print(t("order_rejected", err=e))
        return False
    print(f"  Kraken: {res}")
    return True


# ---------------------------------------------------------------- logging
def read_log():
    if not LOG_FILE.exists():
        return []
    with LOG_FILE.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows if rows and "pair" in rows[0] else []


def log_rows(rows):
    if LOG_FILE.exists():
        with LOG_FILE.open(encoding="utf-8") as f:
            header = f.readline().strip().split(",")
        if header != LOG_FIELDS and "pair" in header:  # older log version - add the new columns
            old = read_log()
            with LOG_FILE.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=LOG_FIELDS, restval="", extrasaction="ignore")
                w.writeheader()
                w.writerows(old)
        elif header != LOG_FIELDS:  # single-pair log from an old version - move it aside
            LOG_FILE.rename(LOG_FILE.with_name(f"decisions_old_{datetime.now():%Y%m%d%H%M%S}.csv"))
    new = not LOG_FILE.exists()
    with LOG_FILE.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def report(cfg, state):
    rows = read_log()
    if not rows:
        print(t("no_history"))
        return
    days = {r["date"][:10] for r in rows}
    eq0, eq1 = float(rows[0]["equity_eur"]), float(rows[-1]["equity_eur"])
    trades = sum(1 for r in rows if r["executed"] in ("buy", "sell"))
    print(t("report_days", days=len(days), trades=trades))
    print(t("report_bot", eq0=eq0, eq1=eq1, pct=(eq1 / eq0 - 1) * 100))
    for pair in sorted({r["pair"] for r in rows}):
        prs = [r for r in rows if r["pair"] == pair]
        bought = sum(1 for r in prs if r["executed"] == "buy")
        sold = sum(1 for r in prs if r["executed"] == "sell")
        print(t("report_pair", pair=pair, bought=bought, sold=sold))

    candles, prices = {}, {}
    for pair in cfg["pairs"]:
        got = optional(f"kraken {pair}", fetch_ohlc, pair, 720)
        if got:
            candles[pair], prices[pair] = got
        else:  # offline: the last price from the log
            last = [r for r in rows if r["pair"] == pair]
            if last:
                prices[pair] = float(last[-1]["price"])
    print()
    for line in evaluation_lines(evaluation(cfg, state, rows, candles, prices)):
        print(line)


# ---------------------------------------------------------------- performance
HORIZONS = (1, 3, 7)  # after how many days the model's calls are settled
P_UP_BUCKETS = ((0, .35), (.35, .45), (.45, .55), (.55, .65), (.65, 1.01))
MIN_SETTLED = 30  # below this the results are mostly noise


def row_ts(r):
    return datetime.strptime(r["date"], "%Y-%m-%d %H:%M").timestamp()


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def rnd(x, n=2):
    return round(x, n) if x is not None else None


def price_after(series, ts, days, tol_h=8):
    """Price approx. N days after ts: the nearest point of series [(ts, price)] if within ±tol_h."""
    target = ts + days * 86400
    best = min(series, key=lambda p: abs(p[0] - target))
    return best[1] if abs(best[0] - target) <= tol_h * 3600 else None


def evaluate_signals(rows, candles=None, prices=None):
    """Settles the model's calls from the log: what the price did 1/3/7 days later. The later price comes
    from later log rows (the cycle runs daily at the same time) or, when missing, from the close of a
    Kraken daily candle (accurate to a few hours)."""
    series = {}
    for r in rows:
        series.setdefault(r["pair"], []).append((row_ts(r), float(r["price"])))
    for pair, cs in (candles or {}).items():
        series.setdefault(pair, []).extend((c["t"] + 86400, c["c"]) for c in cs)
    for pair, p in (prices or {}).items():
        series.setdefault(pair, []).append((time.time(), p))

    sigs = []
    for r in rows:
        ts, p0 = row_ts(r), float(r["price"])
        rets = {}
        for n in HORIZONS:
            p = price_after(series[r["pair"]], ts, n)
            rets[n] = (p / p0 - 1) * 100 if p else None
        sigs.append({"action": r["model_action"], "rets": rets,
                     "p_up": float(r["p_up_3d"]) if r.get("p_up_3d") else None})

    by_action = {}
    for a in ("buy", "hold", "sell"):
        s = [x for x in sigs if x["action"] == a]
        if not s:
            continue
        st = {"signals": len(s)}
        for n in HORIZONS:
            st[f"avg_{n}d_pct"] = rnd(mean(x["rets"][n] for x in s if x["rets"][n] is not None))
        r3 = [x["rets"][3] for x in s if x["rets"][3] is not None]
        st["settled_3d"] = len(r3)
        st["up_3d_pct"] = rnd(mean(100.0 * (x > 0) for x in r3), 1)
        by_action[a] = st

    probs = [(x["p_up"], x["rets"][3] > 0) for x in sigs
             if x["p_up"] is not None and x["rets"][3] is not None]
    brier = mean((p - up) ** 2 for p, up in probs)
    buckets = []
    for lo, hi in P_UP_BUCKETS:
        b = [(p, up) for p, up in probs if lo <= p < hi]
        if b:
            buckets.append({"p_range": f"{lo:.2f}-{min(hi, 1):.2f}", "n": len(b),
                            "avg_p": rnd(mean(p for p, _ in b)),
                            "actual_up_pct": rnd(mean(100.0 * up for _, up in b), 1)})
    return {"by_action": by_action,
            "p_up_3d": {"settled": len(probs), "brier": rnd(brier, 4),
                        "brier_skill_pct": rnd((1 - brier / 0.25) * 100, 1) if brier is not None else None,
                        "hit_rate_pct": rnd(mean(100.0 * ((p > 0.5) == up) for p, up in probs if p != 0.5), 1),
                        "buckets": buckets}}


def shadow_strategies(cfg, rows, candles, prices, sma_days=50):
    """Simple benchmark strategies since the first decision in the log, with fees and slippage:
    buy and hold, and trend (in the market while the daily close is above SMA50, decided at the close)."""
    cost = (cfg["fee_pct"] + cfg.get("slippage_pct", 0.1)) / 100
    per_pair = {}
    for pair, cs in candles.items():
        first = next((r for r in rows if r["pair"] == pair), None)
        if not first or pair not in prices or len(cs) < sma_days:
            continue
        t0, p0 = row_ts(first), float(first["price"])
        closes = [c["c"] for c in cs] + [prices[pair]]
        times = [c["t"] + 86400 for c in cs] + [time.time()]
        start = next((i for i, ts in enumerate(times) if ts > t0), len(closes) - 1)

        def above_sma(i):
            return i >= sma_days - 1 and closes[i] > sum(closes[i - sma_days + 1:i + 1]) / sma_days

        v, last, inside = 1.0, p0, start > 0 and above_sma(start - 1)
        if inside:
            v *= 1 - cost
        for i in range(start, len(closes)):
            if inside:
                v *= closes[i] / last
            last = closes[i]
            if i < len(closes) - 1 and above_sma(i) != inside:
                v *= 1 - cost
                inside = not inside
        per_pair[pair] = {"buy_and_hold_pct": rnd((prices[pair] / p0 * (1 - cost) - 1) * 100),
                          "trend_pct": rnd((v - 1) * 100), "trend_in_market": inside}
    if not per_pair:
        return None
    return {"since": rows[0]["date"][:10], "trend_rule": t("trend_rule", days=sma_days),
            "buy_and_hold_pct": rnd(mean(p["buy_and_hold_pct"] for p in per_pair.values())),
            "trend_pct": rnd(mean(p["trend_pct"] for p in per_pair.values())),
            "per_pair": per_pair}


def evaluation(cfg, state, rows, candles, prices):
    runs = {r["date"]: (float(r["eur"]), float(r["equity_eur"])) for r in rows}
    ev = {"updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
          "bot": {"return_pct": rnd((equity_of(state, prices) / state["start_eur"] - 1) * 100),
                  "avg_exposure_pct": rnd(mean((1 - e / q) * 100 for e, q in runs.values() if q), 1),
                  "cycles": len(runs)},
          **evaluate_signals(rows, candles, prices)}
    if candles and rows:
        ev["shadow"] = shadow_strategies(cfg, rows, candles, prices)
    return ev


def evaluation_lines(ev):
    def f(x, unit="%"):
        return "-" if x is None else f"{x:+.2f}{unit}"

    b, s, c = ev["bot"], ev.get("shadow"), ev["p_up_3d"]
    out = [t("ev_bot", ret=f(b["return_pct"]), exposure=b["avg_exposure_pct"], cycles=b["cycles"])]
    if s:
        out.append(t("ev_shadow", since=s["since"], bh=f(s["buy_and_hold_pct"]), rule=s["trend_rule"],
                     trend=f(s["trend_pct"])))
    out.append(t("ev_header"))
    for a, st in ev["by_action"].items():
        up = "-" if st["up_3d_pct"] is None else f"{st['up_3d_pct']:.0f}%"
        out.append(t("ev_action", action=a, n=st["signals"], up=up, settled=st["settled_3d"],
                     rets=" / ".join(f(st[f"avg_{n}d_pct"]) for n in HORIZONS)))
    if c["settled"]:
        hit = "-" if c["hit_rate_pct"] is None else f"{c['hit_rate_pct']:.0f}%"
        out.append(t("ev_calibration", n=c["settled"], hit=hit, brier=c["brier"], skill=f(c["brier_skill_pct"])))
        for bk in c["buckets"]:
            out.append(t("ev_bucket", range=bk["p_range"], n=bk["n"], avg=bk["avg_p"], up=bk["actual_up_pct"]))
    else:
        out.append(t("ev_nothing"))
    if c["settled"] < MIN_SETTLED:
        out.append(t("ev_noise", n=MIN_SETTLED))
    return out


# ---------------------------------------------------------------- Home Assistant
def ha_conf(cfg):
    ha = cfg.get("home_assistant") or {}
    if not ha.get("enabled"):
        return None
    url = os.environ.get("HA_URL") or ha.get("url")  # the add-on sets HA_URL and HA_TOKEN itself
    token = os.environ.get("HA_TOKEN")
    if not url or not token:
        print(t("ha_missing"))
        return None
    return {**ha, "url": url.rstrip("/"), "token": token}


def ha_post(ha, path, body):
    r = requests.post(f"{ha['url']}/api/{path}", json=body, timeout=10,
                      headers={"Authorization": f"Bearer {ha['token']}"})
    r.raise_for_status()


def ha_set(ha, entity, value, name, icon, unit=None, **attrs):
    attrs = {"friendly_name": name, "icon": icon, **attrs}
    if unit:
        attrs.update(unit_of_measurement=unit, state_class="measurement")
    ha_post(ha, f"states/{entity}", {"state": value, "attributes": attrs})


def fetch_price(pair):
    r = requests.get(f"{KRAKEN}/0/public/Ticker", params={"pair": pair}, headers=UA, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"Kraken: {data['error']}")
    return float(next(iter(data["result"].values()))["c"][0])  # c = last trade


def ha_publish(cfg, state, prices, mode, new_rows=None):
    """Sends the portfolio and the decision log to Home Assistant. An HA error never stops the bot."""
    ha = ha_conf(cfg)
    if not ha:
        return
    try:
        p = ENTITY
        equity = equity_of(state, prices)
        pnl = (equity / state["start_eur"] - 1) * 100
        common = {"mode": mode, "updated": datetime.now().strftime("%Y-%m-%d %H:%M")}
        ha_set(ha, f"{p}_equity", round(equity, 2), t("ha_equity"), "mdi:wallet", "EUR",
               start_eur=state["start_eur"], eur_free=round(state["eur"], 2),
               halted_until=state.get("halted_until"), **common)
        ha_set(ha, f"{p}_return", round(pnl, 2), t("ha_return"), "mdi:chart-line", "%", **common)
        ha_set(ha, f"{p}_free_eur", round(state["eur"], 2), t("ha_free_eur"), "mdi:cash", "EUR")
        for pair, price in prices.items():
            pos = state["positions"].get(pair, empty_position())
            entry, first = pos["entry_price"], state["first_prices"].get(pair)
            ha_set(ha, f"{p}_{pair.lower()}", round(pos["coin"] * price, 2), f"Kraken bot {pair}",
                   "mdi:bitcoin", "EUR", coin=pos["coin"], price=price, entry_price=round(entry, 2) if entry else None,
                   entry_source=pos.get("entry_source"), stop_price=pos.get("stop_price"),
                   position_pnl_pct=round((price / entry - 1) * 100, 2) if entry else None,
                   buy_and_hold_pct=round((price / first - 1) * 100, 2) if first else None)

        log = read_log()
        last_date = log[-1]["date"] if log else None
        keep = ("date", "pair", "price", "model_action", "confidence", "p_up_3d", "executed", "rule")
        ha_set(ha, f"{p}_decision", last_date or t("ha_no_decision"), t("ha_decision"), "mdi:robot",
               summary=state.get("last_summary", ""),
               decisions=[{k: r.get(k) for k in keep + ("reasoning",)} for r in log if r["date"] == last_date],
               history=[{k: r.get(k) for k in keep} for r in log[-30:]])

        ev = state.get("evaluation")
        if ev:  # computed in the daily cycle; entity state = direction hit rate of p_up_3d
            hit = ev["p_up_3d"]["hit_rate_pct"]
            ha_set(ha, f"{p}_performance", hit if hit is not None else "unknown", t("ha_performance"),
                   "mdi:bullseye-arrow", "%", **ev)

        for r in new_rows or []:
            ha_post(ha, "services/logbook/log",
                    {"name": f"Kraken bot {r['pair']}", "entity_id": f"{p}_decision",
                     "message": f"model {r['model_action']} ({r['confidence']:.2f}) -> {r['executed']}"
                                f" ({r['rule']}). {r['reasoning'][:250]}"})
            if r["executed"] in ("buy", "sell") and ha.get("notify"):
                domain, service = ha["notify"].split(".", 1)
                ha_post(ha, f"services/{domain}/{service}",
                        {"title": f"Kraken bot [{mode}]: {r['executed']} {r['pair']}",
                         "message": t("ha_notify_message", qty=r["qty"], price=r["price"], rule=r["rule"],
                                      equity=equity)})
        print(t("ha_updated", url=ha["url"]))
    except Exception as e:
        print(t("ha_error", err=e))


# ---------------------------------------------------------------- offline test
def fake_market(start_price):
    price, closed = start_price, []
    start = int(time.time()) - 61 * 86400
    for i in range(60):
        o = price
        price *= math.exp(random.gauss(0, 0.03))
        closed.append({"t": start + i * 86400, "o": o, "h": max(o, price) * 1.01,
                       "l": min(o, price) * 0.99, "c": price, "v": random.uniform(800, 1500)})
    return closed, price * math.exp(random.gauss(0, 0.01))


def fake_signal():
    return {"action": random.choice(["buy", "sell", "hold"]),
            "confidence": round(random.uniform(0.3, 0.9), 2),
            "p_up_3d": round(random.uniform(0.35, 0.65), 2),
            "size_pct": random.choice([25, 50, 100]),
            "stop_price": None,
            "reasoning": t("offline_reasoning")}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--ha", action="store_true")
    args = ap.parse_args()

    load_env()
    cfg = load_config()
    set_language(cfg)
    pairs = cfg["pairs"]
    live = args.live
    if live and cfg.get("mode") != "live":
        sys.exit(t("live_refused"))
    if live:
        use_live_files()
    mode = mode_label(cfg, live)

    state = load_state(cfg)
    if args.report:
        return report(cfg, state)
    if args.ha:
        prices = {pair: fetch_price(pair) for pair in pairs}
        info = sync_live(cfg, state, prices) if live else None
        rows = guard(cfg, state, prices, live, info)
        if rows:
            log_rows(rows)
        if live or rows:
            save_state(state)
        return ha_publish(cfg, state, prices, mode, rows)
    print(t("cycle_header", now=f"{datetime.now():%Y-%m-%d %H:%M}", pairs=", ".join(pairs), mode=mode.upper()))

    markets, prices, candles, closed_all = {}, {}, {}, {}
    for i, pair in enumerate(pairs):
        if args.offline:
            closed, price = fake_market(60000.0 / (i * 10 + 1))
        else:  # 720 days (Kraken's maximum): SMA200, the yearly picture, settling the model's calls
            closed, price = fetch_ohlc(pair, 720)
            candles[pair] = closed
        closed_all[pair] = closed
        prices[pair] = price
        markets[pair] = indicators(closed, price)
        state["first_prices"].setdefault(pair, price)
        m = markets[pair]
        print(t("price_line", pair=pair, price=price, d1=m["change_1d_pct"], d7=m["change_7d_pct"],
                sma200=m["pct_vs_sma200"], atr=m["atr14_pct"]))
    for pair, rs in relative_strength(closed_all, prices).items():
        markets[pair]["vs_btc"] = rs
        print(t("vs_btc_line", pair=pair, d7=rs["change_7d_pct"], d30=rs["change_30d_pct"]))
    news = [t("offline_news")] if args.offline else fetch_news(cfg["news_feeds"])
    print(t("news_count", n=len(news)))
    pair_info = sync_live(cfg, state, prices) if live else None
    context = {"own_recent_decisions": recent_decisions(pairs, prices),
               "own_track_record": optional("track record", evaluate_signals, read_log(), candles,
                                            None if args.offline else prices)}
    if not args.offline:
        context["derivatives"] = optional("futures", fetch_derivatives, pairs, state)
        context["fear_greed"] = optional("fear&greed", fetch_fear_greed)
        context["market_overview"] = optional("coingecko", fetch_market_overview, state)
        context["implied_vol"] = optional("deribit", fetch_implied_vol, pairs, closed_all)
    if context.get("market_overview"):
        mo = context["market_overview"]
        print(t("market_line", btc=mo["btc_dominance_pct"], stable=mo["stablecoin_share_pct"],
                cap=mo["total_market_cap_change_24h_pct"]))
    for pair, iv in (context.get("implied_vol") or {}).items():
        print(t("dvol_line", pair=pair, dvol=iv["dvol_now"], move=iv["implied_daily_move_pct"],
                realized=iv["realized_vol_30d_annual_pct"]))
    for pair, d in (context.get("derivatives") or {}).items():
        print(t("funding_line", pair=pair, funding=d["funding_8h_pct"], oi=d["open_interest_usd_m"],
                oi_chg=d["open_interest_change_pct"]))
    if context.get("fear_greed"):
        print(f"  Fear & Greed: {context['fear_greed']['today']} ({context['fear_greed']['label']})")

    # positions outside the pair list (e.g. after removing a pair from config.json) - no price, no value
    for pair, p in state["positions"].items():
        if pair not in pairs and p["coin"] > 0:
            print(t("untracked_position", pair=pair, coin=p["coin"]))

    equity = equity_of(state, prices)
    portfolio = {"eur_free": round(state["eur"], 2), "equity_eur": round(equity, 2),
                 "positions": {pair: {"coin": state["positions"][pair]["coin"],
                                      "entry_price": state["positions"][pair]["entry_price"],
                                      "stop_price": state["positions"][pair].get("stop_price"),
                                      "value_eur": round(state["positions"][pair]["coin"] * prices[pair], 2)}
                               for pair in pairs}}

    if args.offline:
        signals, summary = {pair: fake_signal() for pair in pairs}, "OFFLINE TEST"
    else:
        signals, summary = ask_claude(cfg, markets, news, portfolio, context)
    print(t("model_summary", summary=summary))

    # In live the budget during the cycle is tracked on a copy (an estimate after orders) so several buys
    # at once cannot exceed the limits; at the end the real Kraken balance is read anyway.
    book = copy.deepcopy(state) if live else state
    halt = check_halt(cfg, state, prices)
    book["halted_until"] = state.get("halted_until")

    orders = {}
    for pair in pairs:
        exit_order = protective_exit(cfg, book["positions"][pair], prices[pair])
        if exit_order:
            orders[pair] = exit_order
        elif halt:
            orders[pair] = {"side": "none", "why": halt}
    # sells first (they free EUR and exposure room), then buys
    ordered = sorted(pairs, key=lambda p: 0 if signals[p]["action"] == "sell" else 1)

    rows, now_s = [], datetime.now().strftime("%Y-%m-%d %H:%M")
    for pair in ordered:
        sig, price = signals[pair], prices[pair]
        order = orders.get(pair) or decide(cfg, book, pair, prices, sig, markets[pair].get("atr14"))
        if order["side"] == "none" and sig["action"] != "sell":
            raised = tighten_stop(cfg, book["positions"][pair], sig, price, markets[pair].get("atr14"))
            if raised:
                order["why"] += t("why_stop_set", stop=raised)
        p_up = "-" if sig["p_up_3d"] is None else f"{sig['p_up_3d']:.2f}"
        print(t("decision_line", pair=pair, action=sig["action"], conf=sig["confidence"], size=sig["size_pct"],
                p_up=p_up, side=order["side"], why=order["why"]))
        print(f"             {sig['reasoning']}")
        fill, qty = None, 0.0
        if order["side"] in ("buy", "sell"):
            if live and not live_execute(cfg, pair_info[pair], order, price):
                order = {"side": "error", "why": t("why_order_failed", why=order["why"])}
            else:
                if live and cfg.get("live_validate_only", True):
                    order["why"] += t("why_validate_buy" if order["side"] == "buy" else "why_validate_sell")
                fill, qty = paper_execute(cfg, book, pair, order, price)
                print(t("fill_line", mode=mode, side=order["side"], qty=qty, fill=fill))
        rows.append({"date": now_s, "pair": pair, "price": round(price, 6),
                     "model_action": sig["action"], "confidence": sig["confidence"],
                     "p_up_3d": sig["p_up_3d"], "size_pct": sig["size_pct"], "executed": order["side"],
                     "rule": order["why"], "qty": round(qty, 8), "eur": 0,
                     "coin": round(book["positions"][pair]["coin"], 8), "equity_eur": 0,
                     "reasoning": sig["reasoning"]})

    if live:
        try:
            time.sleep(2)  # give market orders a moment to settle
            for pair in pairs:  # stops set in this cycle only live in the working copy
                state["positions"][pair]["stop_price"] = book["positions"][pair].get("stop_price")
            sync_live(cfg, state, prices)
            book = state
        except Exception as e:
            print(t("balance_read_failed", err=e))
    equity = equity_of(book, prices)
    for row in rows:
        row["eur"], row["equity_eur"] = round(book["eur"], 2), round(equity, 2)
    state["last_summary"] = summary
    log_rows(rows)
    ev = optional("evaluation", evaluation, cfg, book, read_log(), candles, prices)
    if ev:
        state["evaluation"] = book["evaluation"] = ev
        for line in evaluation_lines(ev):
            print(f"  {line}")
    save_state(state)
    print(t("equity_line", equity=equity, start=state["start_eur"]))
    ha_publish(cfg, book, prices, mode, rows)


if __name__ == "__main__":
    main()
