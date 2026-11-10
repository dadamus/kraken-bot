"""English / Polish texts for what the user reads in the panel, Home Assistant and notifications. The log stays
English; the model's reasoning follows the language too (the prompt asks for it)."""
import os

NAMES = {"en": "English", "pl": "Polish"}
TEXTS = {
    "en": {
        "title": "Intraday paper", "subtitle": "Simulation only - no orders are sent anywhere",
        "updated": "Updated", "book_model": "model", "book_rules": "rules",
        "equity": "Equity (model book)", "return": "Return (model book)", "rules_return": "Return (rules book)",
        "vs_hold": "vs holding", "today": "Today (model book)", "green_days": "Green days (model book)",
        "costs": "Costs paid (model book)", "spread": "spread", "swap": "swap", "model_cost": "Model cost this month",
        "summary": "Model's view", "halted": "Model book: new positions halted until {until}",
        "regimes": "Today's regimes", "pair": "Pair", "model": "Model", "rule": "Rule",
        "last_step": "Last hourly step", "reasoning": "Reasoning", "no_regimes": "No regimes yet - the first daily cycle runs at {run_time}.",
        "positions": "Open positions", "no_positions": "No open positions.", "book": "Book", "strategy": "Strategy",
        "side": "Side", "entry": "Entry", "price": "Price", "stop": "Stop", "take_profit": "Take-profit",
        "unrealized": "Unrealised", "opened": "Opened",
        "trades": "Closed trades", "no_trades": "No closed trades yet.", "closed": "Closed", "reason": "Reason",
        "move": "Move", "net": "Net", "hours": "Hours",
        "stats": "Track record by strategy", "n": "Trades", "win_rate": "Win rate", "gross": "Before costs",
        "avg_r": "Avg R", "days": "Daily results", "date": "Date", "no_days": "No completed day yet.",
        "regime_settle": "Regimes settled against the next day", "regime": "Regime", "avg_move": "Avg move",
        "in_atr": "|move| in daily ATRs", "right": "Right",
        "backtest": "Backtest (the rule's regime, past hourly candles)",
        "backtest_none": "No backtest yet - it runs when the add-on starts and then once a week.",
        "backtest_line": "{start} - {end}: {ret:+}% ({hold:+}% holding), {n} trades, net {net} EUR, before costs {gross} EUR, "
                         "green days {green}/{days}. Computed {date}.",
        "notify_open": "Intraday paper: {side} {pair} ({strategy})",
        "notify_open_msg": "{qty} @ {price}, SL {stop}, TP {tp}. {why}",
        "notify_close": "Intraday paper: closed {side} {pair} ({reason})",
        "notify_close_msg": "Net {net} EUR ({move}% move). Equity {equity} EUR.",
        "notify_daily": "Intraday paper: yesterday",
        "notify_daily_msg": "Model book {model} EUR, rules book {rules} EUR. Equity {eq_model} / {eq_rules} EUR. {summary}",
        "alert_title": "Intraday paper needs attention",
    },
    "pl": {
        "title": "Intraday paper", "subtitle": "Tylko symulacja - żadne zlecenia nigdzie nie idą",
        "updated": "Odświeżono", "book_model": "model", "book_rules": "reguła",
        "equity": "Kapitał (model)", "return": "Wynik (model)", "rules_return": "Wynik (reguła)",
        "vs_hold": "vs trzymanie", "today": "Dziś (model)", "green_days": "Dni na plusie (model)",
        "costs": "Zapłacone koszty (model)", "spread": "spread", "swap": "swap",
        "model_cost": "Koszt modelu w tym miesiącu",
        "summary": "Zdanie modelu", "halted": "Model: nowe pozycje wstrzymane do {until}",
        "regimes": "Dzisiejsze reżimy", "pair": "Para", "model": "Model", "rule": "Reguła",
        "last_step": "Ostatni krok godzinowy", "reasoning": "Uzasadnienie",
        "no_regimes": "Brak reżimów - pierwszy cykl dzienny o {run_time}.",
        "positions": "Otwarte pozycje", "no_positions": "Brak otwartych pozycji.", "book": "Rachunek",
        "strategy": "Strategia", "side": "Strona", "entry": "Wejście", "price": "Cena", "stop": "Stop",
        "take_profit": "Take-profit", "unrealized": "Niezreal.", "opened": "Otwarta",
        "trades": "Zamknięte pozycje", "no_trades": "Brak zamkniętych pozycji.", "closed": "Zamknięta",
        "reason": "Powód", "move": "Ruch", "net": "Netto", "hours": "Godz.",
        "stats": "Skuteczność wg strategii", "n": "Pozycji", "win_rate": "Trafione", "gross": "Przed kosztami",
        "avg_r": "Śr. R", "days": "Wyniki dzienne", "date": "Data", "no_days": "Brak zakończonego dnia.",
        "regime_settle": "Reżimy rozliczone po dniu", "regime": "Reżim", "avg_move": "Śr. ruch",
        "in_atr": "|ruch| w dziennych ATR", "right": "Trafne",
        "backtest": "Backtest (reżim z reguły, historyczne świece godzinowe)",
        "backtest_none": "Brak backtestu - liczy się przy starcie add-onu, potem raz w tygodniu.",
        "backtest_line": "{start} - {end}: {ret:+}% (trzymanie {hold:+}%), {n} pozycji, netto {net} EUR, przed kosztami "
                         "{gross} EUR, dni na plusie {green}/{days}. Policzono {date}.",
        "notify_open": "Intraday paper: {side} {pair} ({strategy})",
        "notify_open_msg": "{qty} @ {price}, SL {stop}, TP {tp}. {why}",
        "notify_close": "Intraday paper: zamknięto {side} {pair} ({reason})",
        "notify_close_msg": "Netto {net} EUR (ruch {move}%). Kapitał {equity} EUR.",
        "notify_daily": "Intraday paper: wczoraj",
        "notify_daily_msg": "Model {model} EUR, reguła {rules} EUR. Kapitał {eq_model} / {eq_rules} EUR. {summary}",
        "alert_title": "Intraday paper wymaga uwagi",
    },
}
LANG = "en"


def set_language(cfg=None):
    global LANG
    lang = (os.environ.get("BOT_LANGUAGE") or (cfg or {}).get("language") or "en").lower()
    LANG = lang if lang in TEXTS else "en"
    return LANG


def language_name():
    return NAMES[LANG]


def t(key, **kw):
    return TEXTS[LANG].get(key, TEXTS["en"][key]).format(**kw)
