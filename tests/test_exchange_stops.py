"""Stop-loss orders resting on Kraken (exchange_stops.py), with a faked private API."""
import pytest

from krakenbot import exchange_stops, guard, kraken, risk
from conftest import make_state

INFO = {"key": "XXBTZEUR", "base": "XXBT", "quote": "ZEUR", "lot_decimals": 8, "pair_decimals": 1,
        "ordermin": 0.00005}
PRICES = {"XBTEUR": 70000.0}


@pytest.fixture
def live_cfg(cfg):
    cfg["pairs"], cfg["live_validate_only"], cfg["mode"] = ["XBTEUR"], False, "live"
    return cfg


@pytest.fixture
def book(monkeypatch):
    """A fake Kraken order book: open orders, closed (filled / cancelled) ones and the call log."""
    k = {"open": {}, "closed": {}, "calls": [], "n": 0, "fail": False}

    def private(path, data):
        name = path.rsplit("/", 1)[-1]
        k["calls"].append((name, dict(data)))
        if k["fail"]:
            raise RuntimeError("Kraken: EAPI:Rate limit exceeded")
        if name == "OpenOrders":
            return {"open": {tx: o for tx, o in k["open"].items() if o.get("userref") == data["userref"]}}
        if name == "AddOrder":
            k["n"] += 1
            tx = f"T{k['n']}"
            k["open"][tx] = {"descr": {"pair": "XBTEUR", "type": data["type"], "ordertype": data["ordertype"],
                                       "price": data["price"]}, "vol": data["volume"], "userref": data["userref"]}
            return {"txid": [tx]}
        if name == "CancelOrder":
            k["closed"][data["txid"]] = {"status": "canceled", "vol_exec": "0", **k["open"].pop(data["txid"])}
            return {"count": 1}
        if name == "QueryOrders":
            return {tx: k["closed"][tx] for tx in data["txid"].split(",") if tx in k["closed"]}
        raise AssertionError(path)

    monkeypatch.setattr(kraken, "kraken_private", private)
    return k


def names(book):
    return [c[0] for c in book["calls"]]


def position(**kw):
    return make_state(eur=0, XBTEUR={"coin": 0.00322323, "entry_price": 65617.0, "stop_price": 67900.0, **kw})


def test_places_a_stop_order_for_the_position(live_cfg, book):
    state = position()
    assert exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES) == []
    add = next(d for n, d in book["calls"] if n == "AddOrder")
    assert add == {"pair": "XXBTZEUR", "type": "sell", "ordertype": "stop-loss", "price": "67900.0",
                   "volume": "0.00322323", "userref": exchange_stops.USERREF}
    assert state["positions"]["XBTEUR"]["exchange_stop"] == {"txid": "T1", "level": 67900.0, "volume": 0.00322323}

    book["calls"].clear()  # nothing changed: the order stays
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    assert names(book) == ["OpenOrders"]


def test_level_is_the_higher_of_model_stop_and_fixed_stop_loss(live_cfg, book):
    state = position(stop_price=None)  # no model stop: 8% below the entry price
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    assert state["positions"]["XBTEUR"]["exchange_stop"]["level"] == pytest.approx(60367.6)
    assert exchange_stops.wanted_level(live_cfg, {"stop_price": None, "entry_price": None}) is None


def test_raised_stop_or_new_volume_replaces_the_order(live_cfg, book):
    state = position()
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    state["positions"]["XBTEUR"]["stop_price"] = 68500.0
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    assert list(book["open"]) == ["T2"] and book["open"]["T2"]["descr"]["price"] == "68500.0"
    assert book["closed"]["T1"]["status"] == "canceled"

    state["positions"]["XBTEUR"]["coin"] = 0.005  # bought more
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    assert list(book["open"]) == ["T3"] and book["open"]["T3"]["vol"] == "0.00500000"
    assert state["positions"]["XBTEUR"]["exchange_stop"]["txid"] == "T3"


def test_filled_order_becomes_a_stop_row(live_cfg, book):
    state = position()
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    # Kraken sold while the bot was not looking; the next sync_live already shows the EUR
    book["closed"]["T1"] = {"status": "closed", "vol_exec": "0.00322323", "price": "67850.0", **book["open"].pop("T1")}
    state["eur"], state["positions"]["XBTEUR"].update(coin=0.0, entry_price=None, stop_price=None)
    rows = exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, {"XBTEUR": 67500.0})
    assert len(rows) == 1
    r = rows[0]
    assert (r["pair"], r["model_action"], r["executed"], r["price"], r["qty"]) == \
        ("XBTEUR", "stop", "sell", 67850.0, 0.00322323)
    assert r["rule"] == "STOP on the exchange (level 67900.0)" and r["cycle"] == "guard"
    assert state["positions"]["XBTEUR"]["exchange_stop"] is None and book["open"] == {}


def test_order_cancelled_elsewhere_is_placed_again_without_a_row(live_cfg, book):
    state = position()
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    book["closed"]["T1"] = {"status": "canceled", "vol_exec": "0", **book["open"].pop("T1")}
    assert exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES) == []
    assert list(book["open"]) == ["T2"]


def test_orders_placed_by_hand_are_left_alone(live_cfg, book):
    book["open"]["MINE"] = {"descr": {"pair": "XBTEUR", "type": "sell", "ordertype": "limit", "price": "90000.0"},
                            "vol": "0.001", "userref": 0}
    exchange_stops.sync(live_cfg, position(), {"XBTEUR": INFO}, PRICES)
    assert "MINE" in book["open"] and "CancelOrder" not in names(book)


def test_extra_bot_orders_are_cleaned_up(live_cfg, book):
    state = position()
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    book["open"]["T9"] = dict(book["open"]["T1"])  # a duplicate, e.g. after a lost response
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    assert list(book["open"]) == ["T1"]


def test_no_order_without_position_below_minimum_or_above_the_price(live_cfg, book):
    exchange_stops.sync(live_cfg, make_state(), {"XBTEUR": INFO}, PRICES)
    exchange_stops.sync(live_cfg, position(coin=0.0001), {"XBTEUR": INFO}, PRICES)  # 7 EUR < min_trade_eur
    exchange_stops.sync(live_cfg, position(), {"XBTEUR": INFO}, {"XBTEUR": 67000.0})  # already below the stop
    assert "AddOrder" not in names(book)


def test_off_in_paper_validation_or_when_disabled(live_cfg, book):
    exchange_stops.sync(live_cfg, position(), {"XBTEUR": INFO}, PRICES, live=False)
    live_cfg["live_validate_only"] = True
    exchange_stops.sync(live_cfg, position(), {"XBTEUR": INFO}, PRICES)
    live_cfg["live_validate_only"], live_cfg["risk"]["exchange_stops"] = False, False
    exchange_stops.sync(live_cfg, position(), {"XBTEUR": INFO}, PRICES)
    assert book["calls"] == []


def test_api_error_never_stops_the_refresh(live_cfg, book, capsys):
    book["fail"] = True
    assert exchange_stops.sync(live_cfg, position(), {"XBTEUR": INFO}, PRICES) == []
    assert "still works" in capsys.readouterr().out


# ---------------------------------------------------------------- the bot's own stop is the backup
def test_own_stop_waits_for_the_exchange_order(live_cfg):
    pos = position()["positions"]["XBTEUR"]
    pos["exchange_stop"] = {"txid": "T1", "level": 67900.0, "volume": 0.00322323}
    assert risk.protective_exit(live_cfg, pos, 67800.0) is None  # the exchange should be selling
    assert risk.protective_exit(live_cfg, pos, 67300.0) is None  # 0.9% below: still the exchange's job
    assert risk.protective_exit(live_cfg, pos, 67200.0)["side"] == "sell"  # >1% below: the order did not work
    assert risk.protective_exit(live_cfg, pos, 75500.0)["why"] == "TAKE-PROFIT"  # take-profit stays with the bot
    pos["exchange_stop"] = None
    assert risk.protective_exit(live_cfg, pos, 67800.0)["side"] == "sell"


def test_bot_sale_cancels_the_resting_stop_first(live_cfg, book, monkeypatch):
    state = position()
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    book["calls"].clear()
    sold = []
    monkeypatch.setattr(kraken, "live_execute", lambda cfg, info, order, price: sold.append(order) or True)
    monkeypatch.setattr(kraken, "sync_live", lambda cfg, st, prices: None)
    rows = guard.guard(live_cfg, state, {"XBTEUR": 67000.0}, True, {"XBTEUR": INFO})  # >1% below 67900
    assert [r["executed"] for r in rows] == ["sell"] and sold
    assert names(book) == ["CancelOrder"] and book["open"] == {}


def test_own_order_is_recognised_by_txid_whatever_the_pair_name(live_cfg, book):
    state = position()
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    book["open"]["T1"]["descr"]["pair"] = "BTC/EUR"  # a different spelling in the listing
    book["calls"].clear()
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    assert names(book) == ["OpenOrders"] and list(book["open"]) == ["T1"]


def test_never_places_a_second_order_while_the_first_still_rests(live_cfg, book, monkeypatch):
    state = position()
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    order = book["open"]["T1"]
    real = kraken.kraken_private

    def hidden(path, data):  # the listing misses the order, but querying it says it is open
        if path.endswith("OpenOrders"):
            return {"open": {}}
        if path.endswith("QueryOrders"):
            return {"T1": {"status": "open", "vol_exec": "0", **order}}
        return real(path, data)

    monkeypatch.setattr(kraken, "kraken_private", hidden)
    exchange_stops.sync(live_cfg, state, {"XBTEUR": INFO}, PRICES)
    assert list(book["open"]) == ["T1"] and state["positions"]["XBTEUR"]["exchange_stop"]["txid"] == "T1"
