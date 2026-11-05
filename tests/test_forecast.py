"""The theoretical 3-day path drawn on the dashboard, and how it reaches Home Assistant."""
import json
import math
import random
import sys
from datetime import datetime

import pytest

from krakenbot import claude, cli, forecast, homeassistant, journal, market
from conftest import BASE_CFG, log_row, make_candles, make_state

NOW = 1_800_000_000_000


def test_no_view_is_a_flat_line_with_a_widening_band():
    path = forecast.forecast_path(100.0, 0.5, 2.0, NOW)
    assert [p[0] for p in path] == [NOW + k * forecast.DAY_MS for k in range(4)]
    assert all(p[1] == pytest.approx(100.0) for p in path)
    widths = [p[3] - p[2] for p in path]
    assert widths[0] == 0 and widths == sorted(widths)
    assert path[-1][3] == pytest.approx(100 * math.exp(0.02 * math.sqrt(3)), rel=1e-5)


def test_direction_and_size_follow_p_up():
    up = forecast.forecast_path(100.0, 0.6, 2.0, NOW)[-1][1]
    strong = forecast.forecast_path(100.0, 0.7, 2.0, NOW)[-1][1]
    down = forecast.forecast_path(100.0, 0.4, 2.0, NOW)[-1][1]
    assert down < 100 < up < strong
    assert math.log(up / 100) == pytest.approx(-math.log(down / 100), rel=1e-3)  # symmetric (prices: 6 digits)


def test_probability_is_consistent_with_the_band():
    """P(up) = p is reproduced by the normal distribution the path describes."""
    end = forecast.forecast_path(100.0, 0.62, 3.0, NOW)[-1]
    mu, sigma = math.log(end[1] / 100), math.log(end[3] / end[1])
    assert 0.5 * (1 + math.erf(mu / sigma / math.sqrt(2))) == pytest.approx(0.62, abs=1e-4)


@pytest.mark.parametrize("p_up, vol", [(None, 2.0), (0.6, None), (0.6, 0)])
def test_no_forecast_without_p_up_or_volatility(p_up, vol):
    assert forecast.forecast_path(100.0, p_up, vol, NOW) == []


def test_chart_uses_closes_and_summarises_the_path():
    closed = make_candles([100.0 + i for i in range(80)])
    c = forecast.chart(closed, 180.0, {"p_up_3d": 0.6}, {"daily_volatility_pct": 2.0}, "2026-11-05 08:05")
    assert len(c["history"]) == forecast.HISTORY_DAYS
    assert c["history"][-1] == [(closed[-1]["t"] + 86400) * 1000, 179.0]  # time of the close, in ms
    assert c["low_3d_pct"] < 0 < c["expected_3d_pct"] < c["high_3d_pct"]
    assert c["made"] == "2026-11-05 08:05" and c["p_up_3d"] == 0.6


def test_chart_falls_back_to_atr_and_stays_small():
    closed = make_candles([100.0] * 400)
    c = forecast.chart(closed, 100.0, {"p_up_3d": 0.55}, {"atr14_pct": 2.0}, "x")
    assert c["forecast"] and len(json.dumps(c)) < 4000  # HA keeps attributes under 16 KB


def test_offline_cycle_stores_a_chart_per_pair(isolated, monkeypatch):
    (isolated / "config.json").write_text(json.dumps(BASE_CFG), encoding="utf-8")
    random.seed(3)
    monkeypatch.setattr(sys, "argv", ["bot.py", "--offline"])
    cli.main()
    charts = json.loads((isolated / "state.json").read_text(encoding="utf-8"))["charts"]
    assert sorted(charts) == sorted(BASE_CFG["pairs"])
    assert all(len(c["forecast"]) == 4 and c["history"] for c in charts.values())


def test_forecast_entity_published_to_home_assistant(cfg, monkeypatch):
    sent = []
    monkeypatch.setattr(homeassistant, "ha_post", lambda ha, path, body: sent.append((path, body)))
    monkeypatch.setenv("HA_URL", "http://ha")
    monkeypatch.setenv("HA_TOKEN", "x")
    cfg["home_assistant"] = {"enabled": True}
    state = make_state()
    state["charts"] = {"XBTEUR": forecast.chart(make_candles([100.0] * 30), 100.0, {"p_up_3d": 0.6},
                                                {"daily_volatility_pct": 2.0}, "2026-11-05 08:05")}
    homeassistant.ha_publish(cfg, state, {"XBTEUR": 100.0, "ETHEUR": 1.0, "SOLEUR": 1.0}, "paper")

    entities = {path: body for path, body in sent}
    fc = entities["states/sensor.kraken_bot_xbteur_forecast"]
    assert fc["state"] == state["charts"]["XBTEUR"]["expected_3d_pct"] > 0
    assert fc["attributes"]["unit_of_measurement"] == "%" and len(fc["attributes"]["forecast"]) == 4
    assert "states/sensor.kraken_bot_etheur_forecast" not in entities  # no chart yet -> no entity


# ---------------------------------------------------------------- charts without the daily cycle
@pytest.fixture
def refresh(isolated, monkeypatch):
    """Runs --ha with fake public candles; the model must never be asked."""
    (isolated / "config.json").write_text(json.dumps(BASE_CFG), encoding="utf-8")
    candles = make_candles([100.0 + i % 5 for i in range(120)])
    monkeypatch.setattr(market, "fetch_ohlc", lambda pair, days: (candles, 104.0))
    monkeypatch.setattr(market, "fetch_price", lambda pair: 104.0)
    monkeypatch.setattr(claude, "ask_claude", lambda *a: pytest.fail("--ha must never ask the model"))
    monkeypatch.setattr(sys, "argv", ["bot.py", "--ha"])

    def run():
        cli.main()
        return json.loads((isolated / "state.json").read_text(encoding="utf-8"))["charts"]
    return run


def test_refresh_builds_missing_charts_from_the_latest_call(refresh):
    journal.log_rows([log_row("2026-11-04 08:05", price=99.0, p_up=0.3),
                      log_row("2026-11-05 19:08", price=101.0, p_up=0.6),
                      log_row("2026-11-05 22:30", price=95.0, action="stop", executed="sell",
                              rule="STOP (level 96)")])
    charts = refresh()

    btc = charts["XBTEUR"]
    assert btc["made"] == "2026-11-05 19:08" and btc["p_up_3d"] == 0.6  # the stop is not a call
    start = int(datetime(2026, 11, 5, 19, 8).timestamp() * 1000)
    assert btc["forecast"][0] == [start, 101.0, 101.0, 101.0] and btc["expected_3d_pct"] > 0
    assert charts["ETHEUR"]["forecast"] == [] and charts["ETHEUR"]["history"]  # no call yet: price only


def test_refresh_keeps_charts_from_the_daily_cycle(isolated, refresh, monkeypatch):
    state = make_state()
    state["charts"] = {pair: {"history": [], "forecast": [], "made": "cycle"} for pair in BASE_CFG["pairs"]}
    (isolated / "state.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(market, "fetch_ohlc", lambda *a: pytest.fail("charts exist - no candles needed"))
    assert all(c["made"] == "cycle" for c in refresh().values())


def test_refresh_without_candles_saves_nothing(isolated, refresh, monkeypatch):
    monkeypatch.setattr(market, "fetch_ohlc", lambda *a: (_ for _ in ()).throw(RuntimeError("Kraken down")))
    cli.main()  # the source is skipped and retried on the next refresh
    assert not (isolated / "state.json").exists()
