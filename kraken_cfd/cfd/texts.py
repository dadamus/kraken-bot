"""English / Polish texts for what the user reads in the panel, Home Assistant and notifications. The log stays
English; the model's reasoning follows the language too (the prompt asks for it)."""
import os

NAMES = {"en": "English", "pl": "Polish"}
TEXTS = {
    "en": {
        "title": "CFD paper", "subtitle": "Simulation only - no orders are sent anywhere",
        "equity": "Equity", "return": "Return", "vs_hold": "vs holding (equal split)", "balance": "Balance",
        "margin": "Margin used", "margin_level": "Margin level", "costs": "Costs paid",
        "spread": "spread", "swap": "swap", "positions": "Open positions", "no_positions": "No open positions.",
        "pair": "Pair", "side": "Side", "qty": "Qty", "entry": "Entry", "price": "Price", "stop": "Stop",
        "take_profit": "Take-profit", "unrealized": "Unrealised", "days": "Days",
        "calls": "Latest calls", "no_calls": "No calls yet - the first cycle runs at {run_time}.",
        "date": "Date", "action": "Call", "confidence": "Conf.", "executed": "Executed", "rule": "Rule",
        "reasoning": "Reasoning", "trades": "Closed trades", "no_trades": "No closed trades yet.",
        "opened": "Opened", "closed": "Closed", "reason": "Reason", "net": "Net", "move": "Move",
        "stats": "Track record", "by_side": "Trades by side", "n": "Trades", "win_rate": "Win rate",
        "avg_r": "Avg R", "calls_settled": "Calls settled after 3 days", "avg_move": "Avg move in call direction",
        "hit_rate": "Hit rate", "brier": "p_up_3d Brier (0.25 = coin flip)", "model_cost": "Model cost this month",
        "summary": "Model's view", "halted": "New positions halted until {until}", "updated": "Updated",
        "notify_open": "CFD paper: {side} {pair}",
        "notify_open_msg": "{qty} @ {price} (margin {margin} EUR), SL {stop}, TP {tp}. {reasoning}",
        "notify_close": "CFD paper: closed {side} {pair} ({reason})",
        "notify_close_msg": "Net {net} EUR ({move}% move, swap {swap}, spread {spread}). Equity {equity} EUR.",
        "alert_title": "CFD paper needs attention",
    },
    "pl": {
        "title": "CFD paper", "subtitle": "Tylko symulacja - żadne zlecenia nigdzie nie idą",
        "equity": "Kapitał", "return": "Wynik", "vs_hold": "vs trzymanie (równy podział)", "balance": "Saldo",
        "margin": "Depozyt zajęty", "margin_level": "Poziom depozytu", "costs": "Zapłacone koszty",
        "spread": "spread", "swap": "swap", "positions": "Otwarte pozycje", "no_positions": "Brak otwartych pozycji.",
        "pair": "Para", "side": "Strona", "qty": "Ilość", "entry": "Wejście", "price": "Cena", "stop": "Stop",
        "take_profit": "Take-profit", "unrealized": "Niezreal.", "days": "Dni",
        "calls": "Ostatnie decyzje", "no_calls": "Brak decyzji - pierwszy cykl o {run_time}.",
        "date": "Data", "action": "Model", "confidence": "Pewn.", "executed": "Wykonano", "rule": "Reguła",
        "reasoning": "Uzasadnienie", "trades": "Zamknięte pozycje", "no_trades": "Brak zamkniętych pozycji.",
        "opened": "Otwarta", "closed": "Zamknięta", "reason": "Powód", "net": "Netto", "move": "Ruch",
        "stats": "Skuteczność", "by_side": "Pozycje wg strony", "n": "Pozycji", "win_rate": "Trafione",
        "avg_r": "Śr. R", "calls_settled": "Decyzje rozliczone po 3 dniach",
        "avg_move": "Śr. ruch w kierunku decyzji", "hit_rate": "Trafność",
        "brier": "Brier p_up_3d (0,25 = rzut monetą)", "model_cost": "Koszt modelu w tym miesiącu",
        "summary": "Zdanie modelu", "halted": "Nowe pozycje wstrzymane do {until}", "updated": "Odświeżono",
        "notify_open": "CFD paper: {side} {pair}",
        "notify_open_msg": "{qty} @ {price} (depozyt {margin} EUR), SL {stop}, TP {tp}. {reasoning}",
        "notify_close": "CFD paper: zamknięto {side} {pair} ({reason})",
        "notify_close_msg": "Netto {net} EUR (ruch {move}%, swap {swap}, spread {spread}). Kapitał {equity} EUR.",
        "alert_title": "CFD paper wymaga uwagi",
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
