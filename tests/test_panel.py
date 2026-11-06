"""The dashboard panel served by the add-on (panel.py): data endpoint, page, access."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from krakenbot import journal, panel
from conftest import BASE_CFG, log_row, make_state

OPTS = {"language": "pl", "live": False, "run_time": "08:05"}


@pytest.fixture
def files(isolated):
    (isolated / "config.json").write_text(json.dumps(BASE_CFG), encoding="utf-8")
    state = make_state(eur=500.0, XBTEUR={"coin": 0.01, "entry_price": 60000.0, "stop_price": 58000.0,
                                          "exchange_stop": {"txid": "T1", "level": 58000.0, "volume": 0.01}})
    state["first_prices"] = {"XBTEUR": 62000.0}
    state["last_cycle"] = {"date": "2026-11-06 07:05", "prices": {"XBTEUR": 61000.0}, "atr": {"XBTEUR": 2000.0}}
    state["extra_cycles"] = {"date": "2000-01-01", "n": 3}  # another day: today's count is 0
    state["macro_calendar_cache"] = {"events": [{"title": "CPI m/m", "country": "USD", "impact": "High",
                                                 "date": "2099-01-01T08:30:00-05:00"}]}
    (isolated / "state.json").write_text(json.dumps(state), encoding="utf-8")
    journal.log_rows([log_row("2026-11-06 07:05", pair=p, price=pr, p_up=0.5)
                      for p, pr in (("XBTEUR", 61000.0), ("ETHEUR", 2000.0), ("SOLEUR", 80.0))])
    return isolated


def test_data_from_the_bot_files(files):
    d = panel.data(OPTS, panel.Prices())  # no network in tests: the last price from the log
    assert d["language"] == "pl" and d["mode"] == "paper" and d["pairs"] == BASE_CFG["pairs"]
    btc = d["positions"]["XBTEUR"]
    assert btc["price"] == 61000.0 and btc["value"] == pytest.approx(610.0)
    assert btc["pnl_pct"] == pytest.approx(1.667, abs=0.001) and btc["exchange_stop"]["level"] == 58000.0
    assert d["portfolio"]["equity"] == pytest.approx(1110.0) and d["portfolio"]["return_pct"] == pytest.approx(11.0)
    tr = d["triggers"]
    assert tr["today"] == 0 and tr["earliest"] == "2026-11-06 10:05"
    assert tr["pairs"]["XBTEUR"]["low"] == 58000.0 and tr["pairs"]["XBTEUR"]["high"] == 64000.0
    assert [e["event"] for e in d["calendar"]] == ["CPI m/m"]
    assert len(d["decisions"]) == 3 and "reasoning" in d["decisions"][0]


def test_data_before_the_first_start(isolated):
    assert panel.data(OPTS, panel.Prices()) == {"error": "no config.json yet"}
    (isolated / "config.json").write_text(json.dumps(BASE_CFG), encoding="utf-8")
    d = panel.data(OPTS, panel.Prices())  # no state, no log, no prices: renders as "no data"
    assert d["decisions"] == [] and d["portfolio"]["equity"] is None and d["triggers"]["pairs"] == {}


def test_live_reads_the_live_files(files):
    (files / "state_live.json").write_text(json.dumps(make_state(eur=42.0)), encoding="utf-8")
    d = panel.data({**OPTS, "live": True}, panel.Prices())
    assert d["mode"] == "live" and d["portfolio"]["eur"] == 42.0 and d["decisions"] == []


@pytest.fixture
def server(files):
    def start(only_from=None):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), panel.handler(OPTS, only_from))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    started = []
    yield start
    for srv in started:
        srv.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers["Content-Type"], r.read()


def test_serves_page_and_data(server):
    base = server()
    status, kind, body = get(base + "/")
    assert status == 200 and kind.startswith("text/html") and b"api/data" in body
    status, kind, body = get(base + "/api/data")
    assert status == 200 and json.loads(body)["positions"]["XBTEUR"]["coin"] == 0.01
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/config.json")
    assert e.value.code == 404


def test_only_the_ingress_proxy_may_connect(server):
    base = server(only_from=panel.INGRESS_PROXY)
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/api/data")
    assert e.value.code == 403


def test_data_never_contains_secrets(files, monkeypatch):
    monkeypatch.setenv("KRAKEN_API_KEY", "secret-key-123")
    body = json.dumps(panel.data({**OPTS, "kraken_api_key": "secret-key-123"}, panel.Prices()))
    assert "secret-key-123" not in body
