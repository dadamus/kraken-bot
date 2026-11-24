"""The Kraken Derivatives client (signing, requests, the order gate) and the contract mapping (venue)."""
import base64
import hashlib
import hmac

import pytest
from .cfd_helpers import NOW
from .kraken_fake import SPECS

from cfd import futures, venue


def test_signature_follows_krakens_recipe_without_the_derivatives_prefix():
    secret = base64.b64encode(b"top secret").decode()
    want = base64.b64encode(hmac.new(b"top secret", hashlib.sha256(b"symbol=PF_XBTUSD" + b"1700" + b"/api/v3/sendorder")
                                     .digest(), hashlib.sha512).digest()).decode()
    assert futures.sign(secret, "/derivatives/api/v3/sendorder", "symbol=PF_XBTUSD", "1700") == want
    assert futures.sign(secret, "/api/history/v3/account-log", "", "1") == futures.sign(
        secret, "/derivatives/api/history/v3/account-log", "", "1")  # the history API has no prefix to strip


def test_private_request_sends_key_nonce_and_signature(monkeypatch):
    monkeypatch.setenv("KRAKEN_FUTURES_API_KEY", "key")
    monkeypatch.setenv("KRAKEN_FUTURES_API_SECRET", base64.b64encode(b"s").decode())
    sent = {}

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return sent.get("answer", {"result": "success", "openPositions": []})

    def get(url, headers=None, timeout=None):
        sent.update(url=url, headers=headers)
        return Resp()

    monkeypatch.setattr(futures.requests, "get", get)
    assert futures.open_positions() == []
    h = sent["headers"]
    assert sent["url"] == "https://futures.kraken.com/derivatives/api/v3/openpositions"
    assert h["APIKey"] == "key" and h["Authent"] == futures.sign(base64.b64encode(b"s").decode(),
                                                                  "/derivatives/api/v3/openpositions", "", h["Nonce"])
    sent["answer"] = {"result": "error", "error": "authenticationError"}
    with pytest.raises(RuntimeError, match="authenticationError"):
        futures.open_positions()


def test_no_key_no_private_call(monkeypatch):
    with pytest.raises(RuntimeError, match="no Kraken derivatives API key"):
        futures.flex_account()


def test_orders_are_refused_unless_the_run_allows_them(monkeypatch):
    calls = []
    monkeypatch.setattr(futures, "private", lambda path, data=None, method="POST": calls.append(data) or
                        {"sendStatus": {"status": "placed", "order_id": "O1"}})
    with pytest.raises(futures.OrderRefused):
        futures.send_order("PF_XBTUSD", "buy", 0.001)
    monkeypatch.setattr(futures, "ALLOW", "reduce")  # winding down: only orders that shrink a position
    with pytest.raises(futures.OrderRefused):
        futures.send_order("PF_XBTUSD", "buy", 0.001)
    futures.send_order("PF_XBTUSD", "sell", 0.001, "stp", stop_price=55000, reduce_only=True, trigger_signal="mark")
    assert calls == [{"orderType": "stp", "symbol": "PF_XBTUSD", "side": "sell", "size": "0.001",
                      "stopPrice": "55000", "reduceOnly": "true", "triggerSignal": "mark"}]


def test_executed_averages_the_fills():
    status = {"orderEvents": [{"type": "EXECUTION", "amount": 0.001, "price": 60000},
                              {"type": "EXECUTION", "amount": 0.003, "price": 60004}, {"type": "PLACE"}]}
    size, price = futures.executed(status)
    assert size == pytest.approx(0.004) and price == pytest.approx(60003)
    assert futures.executed({"orderEvents": []}) == (0.0, None)


def test_contract_specs_eea_limit_and_rounding():
    btc, fx = venue.trim(SPECS["PF_XBTUSD"]), venue.trim(SPECS["PF_EURUSD"])
    assert btc["max_leverage"] == 10  # the EEA retail schedule, not the global 50x / 100x
    assert venue.available(btc) and not venue.available(fx)
    assert venue.why_unavailable(fx, "PF_EURUSD") == "PF_EURUSD is not offered to EEA clients"
    assert venue.why_unavailable(None, "PF_XYZUSD") == "PF_XYZUSD is not listed on Kraken Derivatives"
    assert venue.round_size(btc, 0.0123456) == 0.0123 and venue.min_size(btc) == 0.0001
    assert venue.round_size(btc, 0.00009999) == 0.0
    gold = venue.trim(SPECS["PF_PAXGUSD"])
    assert venue.round_price(gold, 3612.345) == 3612.3 and venue.round_price(btc, 59999.6) == 60000
    assert venue.carry(50600, 55000, 60000) == pytest.approx(55200)  # 8 % below stays 8 % below


def test_symbols_from_config_or_the_default_name():
    cfg = {"pairs": ["XBTEUR", "PAXGEUR"],
           "instruments": {"PAXGEUR": {"futures_symbols": ["PF_PAXGUSD", "PF_XAUUSD"]}}}
    assert venue.symbol(cfg, "XBTEUR") == "PF_XBTUSD"
    assert venue.symbols(cfg, "PAXGEUR") == ["PF_PAXGUSD", "PF_XAUUSD"]
    assert venue.all_symbols(cfg) == ["PF_XBTUSD", "PF_PAXGUSD", "PF_XAUUSD"]


def test_specs_are_fetched_once_a_day():
    fetched = []

    def fetch(symbols):
        fetched.append(list(symbols))
        return {s: SPECS[s] for s in symbols if s in SPECS}

    state = {}
    venue.specs(state, ["PF_XBTUSD"], NOW, fetch)
    venue.specs(state, ["PF_XBTUSD"], NOW, fetch)
    assert len(fetched) == 1
    venue.specs(state, ["PF_XBTUSD", "PF_PAXGUSD"], NOW, fetch)  # a new symbol
    assert fetched[-1] == ["PF_PAXGUSD", "PF_XBTUSD"]
    from datetime import timedelta
    venue.specs(state, ["PF_XBTUSD"], NOW + timedelta(days=2), fetch)
    assert len(fetched) == 3
