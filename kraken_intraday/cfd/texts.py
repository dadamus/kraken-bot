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
        "base_rate": "The price rose in {up}% of cases - always forecasting that share would score Brier {b}.",
        "summary": "Model's view", "halted": "New positions halted until {until}", "updated": "Updated",
        "notify_open": "{label}: {side} {pair}",
        "notify_open_msg": "{qty} @ {price} (margin {margin} EUR), SL {stop}, TP {tp}. {reasoning}",
        "notify_close": "{label}: closed {side} {pair} ({reason})",
        "notify_close_msg": "Net {net} EUR ({move}% move, swap {swap}, spread {spread}). Equity {equity} EUR.",
        "alert_title": "CFD paper needs attention",
        "title_live": "CFD live", "subtitle_live": "Real orders on Kraken Derivatives",
        "subtitle_shadow": "Simulation - the orders Kraken would need are checked and logged, none is sent",
        "kraken": "Kraken Derivatives (shadow)", "kraken_repriced": "Closed paper trades with Kraken's costs",
        "kraken_paper": "Paper net", "kraken_net": "Kraken net (estimate)", "kraken_fee": "fees",
        "kraken_funding": "funding", "kraken_not_tradable": "{n} trade(s) on {pairs} not tradable on Kraken in the"
                                                            " EEA (paper net {net} EUR)",
        "kraken_contract": "Contract", "kraken_avg_spread": "Avg spread", "kraken_paper_spread": "Paper spread",
        "kraken_funding_day": "Funding long %/day", "kraken_paper_swap": "Paper swap long %/day",
        "kraken_wallet": "Derivatives wallet {value} EUR, free margin {free} EUR (checked {at})",
        "kraken_error": "Last Kraken error ({at}): {message}", "kraken_orders": "Latest shadow orders",
        "kraken_kind": "Step", "kraken_size": "Size", "kraken_price": "Contract price", "kraken_slip": "Slippage bp",
        "kraken_ok": "OK", "kraken_why": "Note",
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
        "base_rate": "Cena rosła w {up}% przypadków - stała prognoza równa tej częstości dałaby Brier {b}.",
        "summary": "Zdanie modelu", "halted": "Nowe pozycje wstrzymane do {until}", "updated": "Odświeżono",
        "notify_open": "{label}: {side} {pair}",
        "notify_open_msg": "{qty} @ {price} (depozyt {margin} EUR), SL {stop}, TP {tp}. {reasoning}",
        "notify_close": "{label}: zamknięto {side} {pair} ({reason})",
        "notify_close_msg": "Netto {net} EUR (ruch {move}%, swap {swap}, spread {spread}). Kapitał {equity} EUR.",
        "alert_title": "CFD paper wymaga uwagi",
        "title_live": "CFD live", "subtitle_live": "Prawdziwe zlecenia na Kraken Derivatives",
        "subtitle_shadow": "Symulacja - zlecenia, których wymagałby Kraken, są sprawdzane i zapisywane, żadne nie"
                           " jest wysyłane",
        "kraken": "Kraken Derivatives (tryb cienia)", "kraken_repriced": "Zamknięte pozycje paper z kosztami Krakena",
        "kraken_paper": "Netto paper", "kraken_net": "Netto Kraken (szacunek)", "kraken_fee": "opłaty",
        "kraken_funding": "funding", "kraken_not_tradable": "{n} pozycji na {pairs} niedostępnych na Krakenie w UE"
                                                            " (netto paper {net} EUR)",
        "kraken_contract": "Kontrakt", "kraken_avg_spread": "Śr. spread", "kraken_paper_spread": "Spread paper",
        "kraken_funding_day": "Funding long %/dzień", "kraken_paper_swap": "Swap paper long %/dzień",
        "kraken_wallet": "Portfel derywatów {value} EUR, wolny depozyt {free} EUR (sprawdzono {at})",
        "kraken_error": "Ostatni błąd Krakena ({at}): {message}", "kraken_orders": "Ostatnie zlecenia cienia",
        "kraken_kind": "Krok", "kraken_size": "Wielkość", "kraken_price": "Cena kontraktu", "kraken_slip": "Poślizg pb",
        "kraken_ok": "OK", "kraken_why": "Uwaga",
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
