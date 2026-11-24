"""Price sources: Kraken spot by default, a derivatives contract's mark price for oil (WTIUSD)."""
from datetime import datetime

import pytest
from .cfd_helpers import NOW
from .kraken_fake import FakeFutures

from cfd import account, cycle, guard, journal, report, settings, sources


class Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def candle(t, o, h, l, c, v="0"):
    return {"time": t * 1000, "open": str(o), "high": str(h), "low": str(l), "close": str(c), "volume": v}


@pytest.fixture
def oil_cfg():
    cfg = settings.defaults()
    cfg["home_assistant"]["enabled"] = False
    sources.use(cfg)
    yield cfg
    sources.FUTURES.clear()


def test_the_example_trades_bitcoin_gold_and_oil(oil_cfg):
    assert oil_cfg["pairs"] == ["XBTEUR", "PAXGEUR", "WTIUSD"]
    oil = account.instrument(oil_cfg, "WTIUSD")
    assert (oil["quote"], oil["leverage"], oil["asset_class"], oil["weekend_close"]) == ("USD", 10, "oil", None)
    assert sources.FUTURES == {"WTIUSD": "PF_WTIOILUSD"}  # the other pairs keep Kraken's spot market
    assert account.notional_eur(oil_cfg, "WTIUSD", 10, 80.0, {"EURUSD": 1.1}) == pytest.approx(727.27, abs=0.01)


def test_contract_candles_are_mark_prices_with_the_traded_volume(oil_cfg, monkeypatch):
    now = 1_800_000_000
    asked = []

    def get(url, params=None, headers=None, timeout=None):
        asked.append((url.rsplit("/", 3)[-3:], params["from"]))
        tick = url.split("/")[-3]
        if tick == "mark":
            if params["from"] < now - 3000:  # the first page; the API says there is more
                return Resp({"candles": [candle(now - 3600, 80, 81, 79, 80.5), candle(now - 1800, 80.5, 82, 80, 81)],
                             "more_candles": True})
            return Resp({"candles": [candle(now - 1800, 80.5, 82, 80, 81), candle(now - 60, 81, 81, 80.8, 80.9)],
                         "more_candles": False})
        return Resp({"candles": [candle(now - 3600, 80, 81, 79, 80.4, "12.5"), candle(now - 1800, 80, 81, 80, 81, "3")],
                     "more_candles": False})

    monkeypatch.setattr(sources.requests, "get", get)
    closed, price = sources.contract_candles("PF_WTIOILUSD", 2 / 24, 30, now=now)
    assert [c["t"] for c in closed] == [now - 3600, now - 1800] and price == 80.9  # the open candle is the price
    assert [c["v"] for c in closed] == [12.5, 3.0] and closed[0]["c"] == 80.5  # mark close, traded volume
    assert asked[0][0] == ["mark", "PF_WTIOILUSD", "30m"] and asked[1][1] == now - 1800 + 1


def test_spot_pairs_still_come_from_kraken_spot(oil_cfg, monkeypatch):
    monkeypatch.setattr(sources.market, "fetch_ohlc", lambda pair, days, interval: (["spot " + pair], 1.0))
    monkeypatch.setattr(sources.market, "fetch_price", lambda pair: {"XBTEUR": 55000.0, "EURUSD": 1.1}[pair])
    assert sources.ohlc("XBTEUR", 2, 60) == (["spot XBTEUR"], 1.0)
    FakeFutures().install(monkeypatch, keys=False)
    prices = cycle.fetch_prices({**oil_cfg, "pairs": ["XBTEUR", "WTIUSD"]}, {"positions": {}})
    assert prices == {"XBTEUR": 55000.0, "WTIUSD": 80.0, "EURUSD": 1.1}  # EUR/USD only to value oil in EUR


def test_the_stop_check_takes_oil_from_the_contract(oil_cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(sources, "contract_candles", lambda sym, days, interval, now=None, volume=True:
                        seen.append((sym, interval, volume)) or ([], 80.0))
    guard.fetch_candles("WTIUSD", NOW.timestamp() - 3600, NOW)
    assert seen == [("PF_WTIOILUSD", 1, False)]  # minute candles, prices only


def test_benchmark_holds_oil_by_its_price_and_a_currency_as_cash(oil_cfg):
    oil_cfg["pairs"] = ["WTIUSD", "EURUSD"]
    state = account.new_state(oil_cfg, NOW)
    state["first_prices"] = {"WTIUSD": 80.0, "EURUSD": 1.09}
    b = report.benchmarks(oil_cfg, state, {"WTIUSD": 88.0, "EURUSD": 1.12})
    assert b["hold_return_pct"] == pytest.approx(5.0)  # (1.10 + 1.00) / 2


def test_oil_trades_24_7_like_its_contract(oil_cfg):
    terms = cycle.trading_terms(oil_cfg, datetime(2026, 11, 7, 12, 0))  # a Saturday
    oil = terms["instruments"]["WTIUSD"]
    assert oil["trading_hours"] == "24/7" and oil["market_open_now"] and oil["price_in"] == "USD"


def test_offline_cycle_can_open_oil_without_the_network(oil_cfg, monkeypatch):
    """CI runs the offline cycle: a USD-priced instrument needs a EUR/USD rate there too."""
    monkeypatch.setattr(cycle.model, "fake_signal", lambda: {"action": "long", "confidence": 0.9, "p_up_3d": 0.6,
                                                             "stop_price": None, "take_profit": None,
                                                             "reasoning": "test"})
    state = account.new_state(oil_cfg, NOW)
    cycle.daily_cycle(oil_cfg, state, offline_mode=True, now=NOW)
    assert "WTIUSD" in state["positions"] and journal.read_calls()[-1]["executed"] == "open_long"
