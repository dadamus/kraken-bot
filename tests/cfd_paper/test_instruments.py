"""Gold and EUR/USD next to bitcoin: per-instrument leverage and costs, EUR conversion, weekend hours."""
from datetime import datetime, timedelta

import pytest

from cfd import account, cycle, guard, report, risk

from .cfd_helpers import sig

MON = datetime(2026, 11, 2, 12, 0)  # a Monday
SAT = datetime(2026, 11, 7, 12, 0)
PRICES = {"XBTEUR": 60000.0, "PAXGEUR": 3600.0, "EURUSD": 1.10}


@pytest.fixture
def mcfg(ccfg):
    ccfg["pairs"] = ["XBTEUR", "PAXGEUR", "EURUSD"]
    return ccfg


@pytest.fixture
def mstate(mcfg):
    return account.new_state(mcfg, MON)


@pytest.mark.parametrize("pair,when,is_open", [
    ("XBTEUR", SAT, True),
    ("PAXGEUR", datetime(2026, 11, 6, 22, 59), True),   # Friday before the close
    ("PAXGEUR", datetime(2026, 11, 6, 23, 0), False),
    ("PAXGEUR", datetime(2026, 11, 8, 23, 30), False),  # Sunday night: gold is still closed
    ("PAXGEUR", datetime(2026, 11, 9, 0, 0), True),     # Monday
    ("EURUSD", datetime(2026, 11, 6, 22, 0), False),
    ("EURUSD", datetime(2026, 11, 8, 22, 59), False),
    ("EURUSD", datetime(2026, 11, 8, 23, 0), True),     # Sunday 23:00
])
def test_weekend_hours(mcfg, pair, when, is_open):
    assert account.market_open(mcfg, pair, when) is is_open


def test_eurusd_pnl_is_converted_to_eur(mcfg, mstate):
    account.open_position(mcfg, mstate, "EURUSD", "long", 10000, 1.10, 1.09, 1.13, MON, PRICES)
    tr = account.close_position(mcfg, mstate, "EURUSD", 1.11, "model: flat", MON, PRICES)
    half = 0.015 / 200
    usd = 10000 * (1.11 * (1 - half) - 1.10 * (1 + half))
    assert tr["pnl_eur"] == pytest.approx(usd / 1.11, abs=0.01)  # converted at the closing rate
    assert 80 < tr["pnl_eur"] < 91


def test_margin_uses_each_instruments_leverage(mcfg, mstate):
    account.open_position(mcfg, mstate, "EURUSD", "short", 11000, 1.10, 1.11, 1.08, MON, PRICES)
    account.open_position(mcfg, mstate, "PAXGEUR", "long", 0.5, 3600, 3500, 3800, MON, PRICES)
    account.open_position(mcfg, mstate, "XBTEUR", "long", 0.005, 60000, 57000, 66000, MON, PRICES)
    # EUR/USD: 11000 EUR notional (USD price x qty / rate) / 30, gold 1800 / 20, BTC 300 / 2
    assert account.margin_used(mcfg, mstate, PRICES) == pytest.approx(11000 / 30 + 90 + 150, rel=1e-6)


def test_size_risks_the_same_eur_on_every_instrument(mcfg, mstate):
    eurusd, _ = risk.size(mcfg, mstate, PRICES, "EURUSD", 1.10, 0.011)  # a 1 % stop
    gold, _ = risk.size(mcfg, mstate, PRICES, "PAXGEUR", 3600.0, 36.0)
    for qty, pair, dist in ((eurusd, "EURUSD", 0.011), (gold, "PAXGEUR", 36.0)):
        risk_eur = qty * dist * account.eur_per_quote(mcfg, pair, PRICES[pair], PRICES)
        assert risk_eur == pytest.approx(15.0)  # 1.5 % of 1000 EUR


def test_swap_rates_differ_per_instrument(mcfg, mstate):
    account.open_position(mcfg, mstate, "PAXGEUR", "long", 1, 3600, 3500, 3800, MON, PRICES)
    account.open_position(mcfg, mstate, "EURUSD", "short", 10000, 1.10, 1.11, 1.08, MON, PRICES)
    fee = account.charge_swaps(mcfg, mstate, PRICES, MON + timedelta(days=1))
    assert fee == pytest.approx(3600 * 0.00034556 - 10000 * 0.00000003, rel=1e-4)


def test_gold_stop_does_not_fire_over_the_weekend_and_gaps_on_monday(mcfg, mstate):
    fri = datetime(2026, 11, 6, 20, 0)
    account.open_position(mcfg, mstate, "PAXGEUR", "long", 1, 3600, 3550, 3800, fri, PRICES)
    weekend_wick = {"t": int(SAT.timestamp()), "o": 3560, "h": 3560, "l": 3500, "c": 3555}
    assert guard.check(mcfg, mstate, {"PAXGEUR": 3555}, SAT, lambda *a: ([weekend_wick], 1)) == []
    monday = datetime(2026, 11, 9, 0, 0)
    gap = {"t": int(monday.timestamp()), "o": 3520, "h": 3530, "l": 3510, "c": 3525}
    rows = guard.check(mcfg, mstate, {}, monday + timedelta(minutes=5), lambda *a: ([gap], 1))
    assert rows[0]["reason"] == "STOP"
    assert rows[0]["exit"] == pytest.approx(3520 * (1 - 0.03 / 200))  # filled at the reopen, not at 3550


def test_no_orders_while_the_market_is_closed(mcfg, mstate):
    markets = {p: {"atr14": PRICES[p] * 0.01} for p in mcfg["pairs"]}
    signals = {"XBTEUR": sig("long", 0.9), "PAXGEUR": sig("short", 0.9), "EURUSD": sig("long", 0.9)}
    rows, _, _ = cycle.execute(mcfg, mstate, PRICES, markets, signals, None, SAT)
    executed = {r["pair"]: (r["executed"], r["rule"]) for r in rows}
    assert executed["XBTEUR"][0] == "open_long"
    assert executed["PAXGEUR"] == ("none", "model: short, market closed (weekend)")
    assert executed["EURUSD"][0] == "none"


def test_the_model_sees_each_instruments_terms(mcfg):
    terms = cycle.trading_terms(mcfg, SAT)["instruments"]
    assert terms["EURUSD"]["leverage"] == 30 and terms["EURUSD"]["price_in"] == "USD"
    assert terms["PAXGEUR"]["market_open_now"] is False and terms["XBTEUR"]["market_open_now"] is True
    assert terms["XBTEUR"]["overnight_swap_pct_per_day"] == {"long": 0.097222, "short": 0.027778}


def test_benchmark_holds_eur_for_a_currency_pair(mcfg, mstate):
    mstate["first_prices"] = {"XBTEUR": 50000.0, "PAXGEUR": 3600.0, "EURUSD": 1.0}
    b = report.benchmarks(mcfg, mstate, PRICES)
    assert b["hold_return_pct"] == pytest.approx((1.2 + 1.0 + 1.0) / 3 * 100 - 100, abs=0.01)
