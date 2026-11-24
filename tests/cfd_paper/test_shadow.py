"""Shadow mode: the paper account unchanged, the orders Kraken would need checked and logged, Kraken's costs."""
from datetime import timedelta

import pytest
from .cfd_helpers import NOW, fake_markets, sig
from .kraken_fake import FakeFutures

from cfd import account, cycle, journal, shadow


@pytest.fixture
def scfg(ccfg):
    ccfg["pairs"] = ["XBTEUR", "PAXGEUR", "EURUSD"]
    ccfg["instruments"]["PAXGEUR"]["futures_symbols"] = ["PF_PAXGUSD", "PF_XAUUSD"]
    return ccfg


@pytest.fixture
def kraken(monkeypatch):
    return FakeFutures().install(monkeypatch, keys=False)


PRICES = {"XBTEUR": 55000.0, "PAXGEUR": 3300.0, "EURUSD": 1.09}


def test_open_is_turned_into_the_contract_order(scfg, cstate, kraken):
    before = shadow.snapshot(cstate)
    account.open_position(scfg, cstate, "XBTEUR", "long", 0.01234567, 55000, 50600, 64000, NOW, PRICES)
    rows = shadow.record(scfg, cstate, before, [], PRICES, NOW)
    r = rows[0]
    assert (r["kind"], r["symbol"], r["side"], r["size"]) == ("open", "PF_XBTUSD", "buy", "0.0123")
    assert r["contract_price"] == "60002"  # the ask of the fake book (mid 60000)
    assert r["stop"] == 55200 and r["take_profit"] == 69818  # 8 % under / 16.4 % over the contract's mid
    # paper fill: 55000 + half of the paper's 0.2 % spread; Kraken's ask in spot terms is far closer
    assert r["slippage_bps"] == pytest.approx((55000 * 60002 / 60000 - 55000 * 1.001) / (55000 * 1.001) * 1e4, abs=0.1)
    assert r["margin_eur"] == pytest.approx(0.0123 * 55000 / 10, abs=0.01) and r["ok"] and r["why"] == ""
    assert shadow.read()[0]["symbol"] == "PF_XBTUSD"


def test_eurusd_and_tiny_sizes_are_flagged(scfg, cstate, kraken):
    before = shadow.snapshot(cstate)
    account.open_position(scfg, cstate, "EURUSD", "short", 5000, 1.09, 1.1, 1.07, NOW, PRICES)
    account.open_position(scfg, cstate, "XBTEUR", "long", 0.00005, 55000, 50600, 64000, NOW, PRICES)
    rows = {r["pair"]: r for r in shadow.record(scfg, cstate, before, [], PRICES, NOW)}
    assert not rows["EURUSD"]["ok"] and rows["EURUSD"]["why"] == "PF_EURUSD is not offered to EEA clients"
    assert not rows["XBTEUR"]["ok"] and "below the contract's minimum 0.0001" in rows["XBTEUR"]["why"]


def test_gold_at_20x_notes_krakens_10x(scfg, cstate, kraken):
    before = shadow.snapshot(cstate)
    account.open_position(scfg, cstate, "PAXGEUR", "long", 1.5, 3300, 3250, 3400, NOW, PRICES)
    r = shadow.record(scfg, cstate, before, [], PRICES, NOW)[0]
    assert r["ok"] and r["why"] == "note: paper leverage 20 > Kraken's 10 (more margin)"


def test_closes_and_breakeven_moves_are_logged(scfg, cstate, kraken):
    pos = account.open_position(scfg, cstate, "XBTEUR", "long", 0.01, 55000, 50600, 64000, NOW, PRICES)
    before = shadow.snapshot(cstate)
    pos["stop"], pos["breakeven"] = 55100.0, True
    rows = shadow.record(scfg, cstate, before, [], PRICES, NOW)
    assert rows[0]["kind"] == "stop_move" and rows[0]["side"] == "sell" and rows[0]["stop"] == 60109
    before = shadow.snapshot(cstate)
    tr = account.close_position(scfg, cstate, "XBTEUR", 54000, "STOP", NOW, PRICES)
    r = shadow.record(scfg, cstate, before, [tr], PRICES, NOW)[0]
    assert r["kind"] == "close (STOP)" and r["side"] == "sell" and "slippage_bps" not in r  # a resting order


def test_a_broken_kraken_never_stops_the_paper_account(scfg, cstate, monkeypatch):
    def down(*a, **k):
        raise RuntimeError("503")
    monkeypatch.setattr(shadow.futures, "instruments", down)
    before = shadow.snapshot(cstate)
    account.open_position(scfg, cstate, "XBTEUR", "long", 0.01, 55000, 50600, 64000, NOW, PRICES)
    assert shadow.record(scfg, cstate, before, [], PRICES, NOW) == []
    assert "503" in cstate["kraken"]["error"]["message"]


def test_quotes_are_averaged_next_to_the_paper_costs(scfg, cstate, kraken):
    shadow.sample(scfg, cstate, NOW)
    kraken.half["PF_XBTUSD"] = 4.0
    shadow.sample(scfg, cstate, NOW + timedelta(minutes=5))
    q = shadow.quote_stats(scfg, cstate)
    assert set(q) == {"PF_XBTUSD", "PF_PAXGUSD", "PF_XAUUSD"}  # no EUR/USD contract for EEA clients
    assert q["PF_XBTUSD"]["samples"] == 2 and q["PF_XBTUSD"]["avg_spread_pct"] == pytest.approx(0.01, abs=1e-4)
    assert q["PF_XBTUSD"]["avg_funding_long_pct_day"] == pytest.approx(0.024)
    assert q["PF_XAUUSD"]["traded"] is False and q["PF_PAXGUSD"]["traded"] is True


def trade(pair, side, qty, entry, exit_, opened, closed, pnl, spread, swap):
    net = pnl - swap
    return {"pair": pair, "side": side, "qty": str(qty), "entry": str(entry), "exit": str(exit_),
            "opened": opened, "closed": closed, "reason": "STOP", "pnl_eur": str(pnl), "spread_eur": str(spread),
            "swap_eur": str(swap), "net_eur": str(net)}


def test_paper_trades_repriced_with_krakens_costs(scfg, cstate, kraken):
    # a long of 0.01 BTC held 2 days: 48 hourly fundings of 0.001 % = 0.048 % of the 550 EUR notional
    t = trade("XBTEUR", "long", 0.01, 55000, 56000, "2026-11-01 10:00", "2026-11-03 10:00", 10.0, 1.1, 1.07)
    fx = trade("EURUSD", "short", 5000, 1.09, 1.08, "2026-11-01 10:00", "2026-11-02 10:00", 46.0, 0.7, 0.0)
    out = shadow.reprice(scfg, cstate, [t, fx], NOW + timedelta(days=2))
    row = out["trades"][0]
    fee = (550 + 560) * 0.0005
    spread = (550 + 560) / 2 * (4 / 60000 * 100) / 100
    assert row["fee_eur"] == pytest.approx(fee, abs=0.01) and row["spread_eur"] == pytest.approx(spread, abs=0.01)
    assert row["funding_eur"] == pytest.approx(550 * 0.00001 * 48, abs=0.01)
    assert row["kraken_net_eur"] == pytest.approx(11.1 - fee - spread - 550 * 0.00001 * 48, abs=0.02)
    tot = out["total"]
    assert tot["n"] == 1 and tot["paper_net_eur"] == pytest.approx(8.93)
    assert tot["not_tradable_n"] == 1 and tot["not_tradable_pairs"] == ["EURUSD"]
    assert tot["not_tradable_paper_net_eur"] == 46.0


def test_shadow_cycle_end_to_end(scfg, cstate, kraken, monkeypatch, no_data):
    scfg["pairs"] = ["XBTEUR", "PAXGEUR"]
    monkeypatch.setattr(cycle, "load_markets", fake_markets({"XBTEUR": 55000.0, "PAXGEUR": 3300.0}))
    monkeypatch.setattr(cycle.guard, "fetch_candles", lambda *a: ([], 1))
    cycle.daily_cycle(scfg, cstate, now=NOW, mode="shadow",
                      ask=lambda c, p: ({"XBTEUR": sig("long", 0.9), "PAXGEUR": sig("short", 0.9)}, "s"))
    rows = shadow.read()
    assert [(r["pair"], r["side"], r["ok"]) for r in rows] == [("XBTEUR", "buy", "True"), ("PAXGEUR", "sell", "True")]
    assert set(cstate["kraken"]["quotes"]) == {"PF_XBTUSD", "PF_PAXGUSD", "PF_XAUUSD"}
    assert cstate["evaluation"]["kraken"]["total"]["n"] == 0
    assert [r["executed"] for r in journal.read_calls()] == ["open_long", "open_short"]  # the paper as before

    cycle.daily_cycle(scfg, cstate, now=NOW + timedelta(days=1), mode="shadow",
                      ask=lambda c, p: ({"XBTEUR": sig("flat", 0.9), "PAXGEUR": sig("hold")}, "s2"))
    assert shadow.read()[-1]["kind"] == "close (model: flat)"
    assert cstate["evaluation"]["kraken"]["total"]["n"] == 1


def test_paper_mode_does_not_touch_kraken(scfg, cstate, monkeypatch, no_data):
    scfg["pairs"] = ["XBTEUR"]
    monkeypatch.setattr(cycle, "load_markets", fake_markets({"XBTEUR": 55000.0}))
    cycle.daily_cycle(scfg, cstate, now=NOW, ask=lambda c, p: ({"XBTEUR": sig("long", 0.9)}, "s"))  # no network
    assert shadow.read() == [] and "kraken" not in cstate


def test_wallet_check_with_a_read_only_key(scfg, cstate, monkeypatch):
    fake = FakeFutures().install(monkeypatch, keys=True)
    acc = shadow.check_account(scfg, cstate, PRICES, NOW)
    assert acc["portfolio_eur"] == pytest.approx(1000.0) and acc["available_margin_eur"] == pytest.approx(900.0)
    fake.wallet = 0
    assert shadow.check_account(scfg, cstate, PRICES, NOW + timedelta(minutes=5))["portfolio_eur"] == 1000.0  # hourly
    monkeypatch.setattr(shadow.futures, "private", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("invalidKey")))
    shadow.check_account(scfg, cstate, PRICES, NOW + timedelta(hours=2))
    assert "invalidKey" in cstate["problems"]["kraken_futures_key"]
