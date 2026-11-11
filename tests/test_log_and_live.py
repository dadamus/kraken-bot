"""The decision log, context for the model and talking to Kraken (no network - everything is faked)."""
import csv
import json
import time
from datetime import datetime

import pytest

from krakenbot import claude, config, journal, kraken, portfolio
from conftest import log_row, make_state


# ---------------------------------------------------------------- log
def test_log_roundtrip_with_header(isolated):
    journal.log_rows([log_row("2026-11-04 08:05", p_up=0.52)])
    with config.LOG_FILE.open(encoding="utf-8") as f:
        assert f.readline().strip().split(",") == journal.LOG_FIELDS
    rows = journal.read_log()
    assert len(rows) == 1 and rows[0]["p_up_3d"] == "0.52"


def test_older_log_gets_new_column_without_losing_history(isolated):
    old_fields = [f for f in journal.LOG_FIELDS if f != "p_up_3d"]
    with config.LOG_FILE.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=old_fields)
        w.writeheader()
        w.writerow({k: v for k, v in log_row("2026-10-30 20:01").items() if k in old_fields}
                   | {"reasoning": 'a "quoted" word, and a comma'})
    journal.log_rows([log_row("2026-11-04 08:05", p_up=0.6)])

    rows = journal.read_log()
    assert [r["date"] for r in rows] == ["2026-10-30 20:01", "2026-11-04 08:05"]
    assert rows[0]["p_up_3d"] == "" and rows[0]["reasoning"] == 'a "quoted" word, and a comma'
    assert rows[1]["p_up_3d"] == "0.6"


def test_single_pair_log_is_moved_aside(isolated):
    config.LOG_FILE.write_text("date,price,action\n2026-10-01,1,hold\n", encoding="utf-8")
    journal.log_rows([log_row("2026-11-04 08:05")])
    assert len(list(isolated.glob("decisions_old_*.csv"))) == 1
    assert len(journal.read_log()) == 1


def test_recent_decisions_include_stops_from_the_same_window(isolated):
    rows = [log_row("2026-10-20 08:05", action="stop", executed="sell", rule="STOP (level 70000)")]
    rows += [log_row(f"2026-11-{d:02d} 08:05", price=100 + d, p_up=0.5) for d in range(1, 10)]
    rows.append(log_row("2026-11-08 14:30", action="stop", executed="sell", rule="STOP (level 99)", price=99))
    journal.log_rows(rows)

    out = claude.recent_decisions(["XBTEUR"], {"XBTEUR": 110.0})["XBTEUR"]
    dates = [r["date"] for r in out]
    assert "2026-11-01 08:05" not in dates and "2026-11-03 08:05" in dates  # the last 7 days of calls
    assert "2026-10-20 08:05" not in dates  # an old stop outside the window
    stop = next(r for r in out if r["action"] == "stop")
    assert stop["rule"] == "STOP (level 99)"
    assert stop["price_change_since_pct"] == pytest.approx(11.11, abs=0.01)
    assert "rule" not in out[0]  # ordinary calls have no rule field


def test_last_exit_is_the_last_sale_without_a_purchase_since(isolated):
    journal.log_rows([
        log_row("2026-11-01 08:05", action="buy", executed="buy", price=100),
        log_row("2026-11-03 08:05", action="sell", executed="sell", price=104),
        log_row("2026-11-05 16:55", action="stop", executed="sell", rule="STOP (level 99)", price=98),
        log_row("2026-11-06 08:05", price=95),
        log_row("2026-11-05 08:05", pair="ETHEUR", action="stop", executed="sell", price=50),
        log_row("2026-11-07 08:05", pair="ETHEUR", action="buy", executed="buy", price=48),
        log_row("2026-09-01 08:05", pair="SOLEUR", action="sell", executed="sell", price=10),
    ])
    out = claude.last_exits(["XBTEUR", "ETHEUR", "SOLEUR"], {"XBTEUR": 99.96, "ETHEUR": 49, "SOLEUR": 9},
                            now=datetime(2026, 11, 11, 18, 0))
    assert out["XBTEUR"] == {"date": "2026-11-05 16:55", "price": 98.0, "by": "stop", "price_vs_exit_pct": 2.0}
    assert "ETHEUR" not in out  # bought back since the sale
    assert "SOLEUR" not in out  # older than 30 days


def test_model_sees_the_last_exit_in_its_portfolio(isolated):
    from krakenbot import cli
    journal.log_rows([log_row(datetime.now().strftime("%Y-%m-%d %H:%M"), action="stop", executed="sell",
                              price=67420.5)])
    view = cli.portfolio_view(make_state(eur=335.91), {"XBTEUR": 66848.4, "ETHEUR": 1850, "SOLEUR": 74},
                              ["XBTEUR", "ETHEUR", "SOLEUR"])
    assert view["positions"]["XBTEUR"]["last_exit"]["price"] == 67420.5
    assert view["positions"]["XBTEUR"]["last_exit"]["price_vs_exit_pct"] == pytest.approx(-0.85, abs=0.01)
    assert "last_exit" not in view["positions"]["ETHEUR"]
    assert "last_exit" in claude.system_prompt()


# ---------------------------------------------------------------- entry price from history
INFO = {"key": "XXBTZEUR", "base": "XXBT", "quote": "ZEUR", "lot_decimals": 8, "ordermin": 0.0001}


def trade(kind, vol, cost, fee=0.0, t=1):
    return {"pair": "XXBTZEUR", "type": kind, "vol": str(vol), "cost": str(cost), "fee": str(fee), "time": t}


def test_entry_from_trade_history():
    entry, src = kraken.position_from_trades([trade("buy", 0.01, 700, 2.8)], INFO, "XBTEUR", 0.01, None, 0.0001)
    assert entry == pytest.approx(70280) and src == "history"


def test_entry_mixes_manual_coins_and_bot_trades():
    entry, src = kraken.position_from_trades([trade("buy", 0.01, 702.8)], INFO, "XBTEUR", 0.03, 60000, 0.0001)
    assert entry == pytest.approx((0.02 * 60000 + 702.8) / 0.03) and src == "history+manual"


def test_entry_only_manual():
    assert kraken.position_from_trades([], INFO, "XBTEUR", 0.01, 60000, 0.0001) == (60000, "manual")


def test_selling_keeps_average_entry():
    trades = [trade("buy", 0.02, 1400, t=1), trade("sell", 0.01, 800, t=2)]
    entry, _ = kraken.position_from_trades(trades, INFO, "XBTEUR", 0.01, None, 0.0001)
    assert entry == pytest.approx(70000)


def test_unknown_entry_without_manual_price():
    assert kraken.position_from_trades([], INFO, "XBTEUR", 0.01, None, 0.0001) == (None, None)


# ---------------------------------------------------------------- syncing with Kraken
@pytest.fixture
def fake_kraken(monkeypatch):
    k = {"balance": {"ZEUR": "50.5", "XXBT": "0.01"}, "trades": [], "orders": [], "ledger": [],
         "ledger_calls": []}

    def private(path, data):
        if path.endswith("Balance"):
            return k["balance"]
        if path.endswith("Ledgers"):
            if k.get("ledger_error"):
                raise RuntimeError("Kraken: ['EGeneral:Permission denied']")
            k["ledger_calls"].append(data)
            rows = [x for x in k["ledger"] if x["type"] == data["type"] and float(x["time"]) > data["start"]]
            page = rows[data["ofs"]:data["ofs"] + 50]
            return {"ledger": {f"L{data['ofs'] + n}": x for n, x in enumerate(page)}, "count": len(rows)}
        if path.endswith("AddOrder"):
            if k.get("reject"):
                raise RuntimeError("Kraken: EOrder:Insufficient funds")
            k["orders"].append(data)
            return {"descr": {"order": "ok"}}
        raise AssertionError(path)

    monkeypatch.setattr(kraken, "kraken_private", private)
    monkeypatch.setattr(kraken, "kraken_pairs", lambda pairs: {"XBTEUR": INFO})
    monkeypatch.setattr(kraken, "kraken_trades", lambda: k["trades"])
    return k


def test_sync_uses_real_balance_and_keeps_stop(cfg, fake_kraken):
    cfg["pairs"], cfg["entry_prices"] = ["XBTEUR"], {"XBTEUR": 60000}
    state = make_state(eur=999, XBTEUR={"coin": 5, "entry_price": 1, "stop_price": 65000})
    kraken.sync_live(cfg, state, {"XBTEUR": 70000.0})

    pos = state["positions"]["XBTEUR"]
    assert state["eur"] == 50.5
    assert pos == {"coin": 0.01, "entry_price": 60000, "entry_source": "manual", "stop_price": 65000,
                   "exchange_stop": None, "peak_price": None}
    assert state["start_eur"] == pytest.approx(750.5)  # the first sync sets the reference point
    start = state["start_eur"]

    fake_kraken["balance"] = {"ZEUR": "700", "XXBT": "0"}  # the position was sold
    kraken.sync_live(cfg, state, {"XBTEUR": 70000.0})
    assert state["positions"]["XBTEUR"]["stop_price"] is None
    assert state["start_eur"] == start  # later syncs leave it alone


def ledger(kind, asset, amount, at, fee="0"):
    return {"type": kind, "asset": asset, "amount": str(amount), "fee": fee, "time": at}


def test_deposit_and_withdrawal_move_reference_not_profit(cfg, fake_kraken, capsys):
    cfg["pairs"], cfg["entry_prices"] = ["XBTEUR"], {"XBTEUR": 60000}
    state = make_state()
    kraken.sync_live(cfg, state, {"XBTEUR": 70000.0})  # first sync: reference 750.5, nothing read yet
    assert fake_kraken["ledger_calls"] == []
    since = state["ledger_checked"]

    fake_kraken["ledger"] = [ledger("deposit", "ZEUR", 100, since - 60),  # before the bot started: ignored
                             ledger("deposit", "ZEUR", 100.5, since + 10, fee="0.5"),
                             ledger("withdrawal", "ZEUR", -20, since + 20, fee="0.35"),
                             ledger("deposit", "XXBT", 0.001, since + 30),
                             ledger("deposit", "XDG", 500, since + 40)]  # not a traded pair: ignored
    fake_kraken["balance"] = {"ZEUR": "130.15", "XXBT": "0.011"}
    kraken.sync_live(cfg, state, {"XBTEUR": 70000.0})

    assert state["start_eur"] == pytest.approx(750.5 + 100 - 20.35 + 70)
    assert portfolio.equity_of(state, {"XBTEUR": 70000.0}) == pytest.approx(state["start_eur"])  # no fake profit
    assert [f["type"] for f in state["cash_flows"]] == ["deposit", "withdrawal", "deposit"]
    assert state["ledger_checked"] == since + 40
    assert "Deposit: +100 ZEUR" in capsys.readouterr().out

    start = state["start_eur"]
    kraken.sync_live(cfg, state, {"XBTEUR": 70000.0})  # the same entries are not counted twice
    assert state["start_eur"] == start


def test_existing_state_starts_tracking_flows_from_now(cfg, fake_kraken):
    cfg["pairs"] = ["XBTEUR"]
    state = make_state()
    state["synced_at"], state["start_eur"] = "2026-11-04 08:05", 500.0  # live before this feature
    fake_kraken["ledger"] = [ledger("deposit", "ZEUR", 100, time.time() - 3600)]  # already in start_eur
    kraken.sync_live(cfg, state, {"XBTEUR": 70000.0})
    kraken.sync_live(cfg, state, {"XBTEUR": 70000.0})
    assert state["start_eur"] == 500.0


def test_missing_ledger_permission_only_warns(cfg, fake_kraken, capsys):
    cfg["pairs"] = ["XBTEUR"]
    state = make_state()
    kraken.sync_live(cfg, state, {"XBTEUR": 70000.0})
    since, start = state["ledger_checked"], state["start_eur"]
    fake_kraken["ledger_error"] = True
    kraken.sync_live(cfg, state, {"XBTEUR": 70000.0})
    assert "Query Ledger Entries" in capsys.readouterr().out
    assert state["eur"] == 50.5 and state["start_eur"] == start
    assert state["ledger_checked"] == since  # once the permission is added, nothing is missed


def test_sync_ignores_implausible_manual_price(cfg, fake_kraken, monkeypatch):
    cfg["pairs"] = ["XBTEUR"]
    monkeypatch.setenv("BOT_ENTRY_PRICES", json.dumps({"XBTEUR": 700}))  # total paid instead of price per BTC
    state = make_state()
    kraken.sync_live(cfg, state, {"XBTEUR": 70000.0})
    assert state["positions"]["XBTEUR"]["entry_price"] is None


def test_addon_entry_prices_override_config(cfg, monkeypatch):
    cfg["entry_prices"] = {"XBTEUR": 60000, "ETHEUR": 2000}
    monkeypatch.setenv("BOT_ENTRY_PRICES", json.dumps({"xbteur": 65617}))
    assert kraken.manual_entry_prices(cfg) == {"XBTEUR": 65617.0, "ETHEUR": 2000.0}


def test_live_order_volume_and_validation(cfg, fake_kraken):
    assert kraken.live_execute(cfg, INFO, {"side": "buy", "eur": 100}, 70000.0) is True
    order = fake_kraken["orders"][0]
    assert order["volume"] == "0.00142142"  # reserve for fee and slippage, rounded down
    assert order["validate"] == "true" and order["ordertype"] == "market"

    cfg["live_validate_only"] = False
    kraken.live_execute(cfg, INFO, {"side": "sell", "coin": 0.0123456789}, 70000.0)
    assert fake_kraken["orders"][1]["volume"] == "0.01234567" and "validate" not in fake_kraken["orders"][1]


def test_live_order_below_minimum_or_rejected(cfg, fake_kraken):
    assert kraken.live_execute(cfg, dict(INFO, ordermin=0.01), {"side": "buy", "eur": 100}, 70000.0) is False
    assert fake_kraken["orders"] == []
    fake_kraken["reject"] = True
    assert kraken.live_execute(cfg, INFO, {"side": "buy", "eur": 100}, 70000.0) is False


# ---------------------------------------------------------------- the "keep what you had" benchmark
def test_first_sync_records_the_starting_holdings(cfg, fake_kraken):
    cfg["pairs"] = ["XBTEUR"]
    state = make_state()
    kraken.sync_live(cfg, state, {"XBTEUR": 70000.0})
    hold = state["hold_start"]
    assert hold["eur"] == 50.5 and hold["coins"] == {"XBTEUR": 0.01}

    # the bot sells at 69000 and the price recovers to 72000: keeping the coins would have been better
    fake_kraken["balance"] = {"ZEUR": str(50.5 + 0.01 * 69000 * 0.996), "XXBT": "0"}
    kraken.sync_live(cfg, state, {"XBTEUR": 72000.0})
    vs = portfolio.vs_hold(state, {"XBTEUR": 72000.0})
    assert vs["value"] == pytest.approx(770.5)
    assert vs["diff_eur"] == pytest.approx(50.5 + 687.24 - 770.5, abs=0.01)
    assert vs["diff_pp"] < 0 and vs["return_pct"] == pytest.approx((770.5 / 750.5 - 1) * 100, abs=0.01)


def test_deposits_go_to_both_the_bot_and_the_benchmark(cfg, fake_kraken):
    cfg["pairs"] = ["XBTEUR"]
    state = make_state()
    kraken.sync_live(cfg, state, {"XBTEUR": 70000.0})
    since = state["ledger_checked"]
    fake_kraken["ledger"] = [ledger("deposit", "ZEUR", 100, since + 10),
                             ledger("deposit", "XXBT", 0.001, since + 20)]
    fake_kraken["balance"] = {"ZEUR": "150.5", "XXBT": "0.011"}
    kraken.sync_live(cfg, state, {"XBTEUR": 70000.0})
    assert state["hold_start"]["eur"] == pytest.approx(150.5)
    assert state["hold_start"]["coins"]["XBTEUR"] == pytest.approx(0.011)
    vs = portfolio.vs_hold(state, {"XBTEUR": 60000.0})  # a deposit is neither profit nor loss for either
    assert vs["diff_eur"] == pytest.approx(0) and state["cash_flows"][0]["ts"] == since + 10


def test_older_state_rebuilds_the_starting_holdings_from_trades(cfg, fake_kraken):
    cfg["pairs"] = ["XBTEUR"]
    state = make_state()
    state["synced_at"], state["start_eur"] = "2026-11-05 11:17", 750.5
    state["ledger_checked"] = since = 1_000_000.0
    fake_kraken["trades"] = [trade("buy", 0.005, 300, 1.2, t=since - 100),  # before the start: already in it
                             trade("sell", 0.01, 690, 2.76, t=since + 100)]  # the stop sold everything
    fake_kraken["balance"] = {"ZEUR": str(50.5 + 690 - 2.76), "XXBT": "0"}
    kraken.sync_live(cfg, state, {"XBTEUR": 72000.0})
    hold = state["hold_start"]
    assert hold["eur"] == pytest.approx(50.5) and hold["coins"]["XBTEUR"] == pytest.approx(0.01)
    assert hold["since"] == datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M")
    assert state["start_eur"] == 750.5  # the bot's own reference does not move


def test_paper_state_has_no_hold_benchmark(cfg):
    state = make_state()
    assert portfolio.vs_hold(state, {"XBTEUR": 70000.0, "ETHEUR": 2000.0, "SOLEUR": 80.0}) is None
