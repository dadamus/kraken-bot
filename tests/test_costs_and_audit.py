"""Model API cost tracking (costs.py) and settling stops / extra cycles (evaluation.py)."""
import json
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from krakenbot import claude, cli, costs, evaluation, journal, market
from conftest import BASE_CFG, log_row, make_candles, signal


def usage(model="claude-opus-5-5", tin=10000, tout=2000, read=0, write=0):
    return {"model": model, "input_tokens": tin, "output_tokens": tout,
            "cache_read_tokens": read, "cache_write_tokens": write}


# ---------------------------------------------------------------- costs
def test_cost_follows_the_answering_model(cfg):
    assert costs.cost_usd(cfg, usage()) == pytest.approx(0.04 + 0.04)  # 10k x $4/M + 2k x $20/M
    assert costs.cost_usd(cfg, usage(model="claude-opus-4-8")) == pytest.approx(0.05 + 0.05)
    assert costs.cost_usd(cfg, usage(tin=0, tout=0, read=1_000_000, write=1_000_000)) == pytest.approx(0.4 + 5.0)


def test_unknown_model_falls_back_to_config_model_then_high_price(cfg):
    assert costs.cost_usd(cfg, usage(model="claude-new-thing")) == pytest.approx(0.08)  # cfg model = Opus 5.5
    cfg["model"] = "something-else"
    assert costs.cost_usd(cfg, usage(model="claude-new-thing")) == pytest.approx(0.1 + 0.1)
    cfg["api_prices_usd_per_mtok"] = {"claude-new-thing": [1, 2]}
    assert costs.cost_usd(cfg, usage(model="claude-new-thing")) == pytest.approx(0.01 + 0.004)


def test_record_sums_the_month_and_resets_next_month(cfg):
    state = {}
    costs.record(cfg, state, usage(read=5000), "daily", datetime(2026, 11, 6, 8, 5))
    costs.record(cfg, state, usage(), "extra:stop", datetime(2026, 11, 6, 19, 55))
    u = state["api_usage"]
    assert u["month"] == "2026-11" and u["calls"] == 2 and u["input_tokens"] == 25000
    assert u["cost_usd"] == pytest.approx(0.162) and u["last"]["cycle"] == "extra:stop"
    costs.record(cfg, state, usage(), "daily", datetime(2026, 12, 1, 8, 5))
    u = state["api_usage"]
    assert u["month"] == "2026-12" and u["calls"] == 1 and u["cost_usd"] == pytest.approx(0.08)
    assert u["total_calls"] == 3 and u["total_cost_usd"] == pytest.approx(0.242)


def test_usage_of_a_response():
    resp = SimpleNamespace(model="claude-opus-5-5", usage=SimpleNamespace(
        input_tokens=100, output_tokens=20, cache_read_input_tokens=None, cache_creation_input_tokens=5))
    assert costs.usage_of(resp) == {"model": "claude-opus-5-5", "input_tokens": 100, "output_tokens": 20,
                                    "cache_read_tokens": 0, "cache_write_tokens": 5}
    assert costs.usage_of(SimpleNamespace()) is None


# ---------------------------------------------------------------- stops and cycles in the evaluation
def test_stop_audit_shows_what_happened_after_the_sale():
    rows = [log_row("2026-11-01 08:05", price=100.0, p_up=0.6),
            log_row("2026-11-02 16:55", price=95.0, action="stop", executed="sell", rule="STOP (level 96)"),
            log_row("2026-11-05 16:00", price=90.0, p_up=0.4),   # 3 days after the stop, lower
            log_row("2026-11-09 17:00", price=99.75, p_up=0.5)]  # 7 days after, higher
    ev = evaluation.evaluate_signals(rows)
    st = ev["stops"]
    assert st["stops"] == 1 and st["settled_3d"] == 1 and st["lower_after_3d"] == 1
    assert st["avg_3d_pct"] == pytest.approx(-5.26, abs=0.01) and st["avg_7d_pct"] == pytest.approx(5.0)
    assert st["recent"][0]["rule"] == "STOP (level 96)" and st["recent"][0]["after_1d_pct"] is None
    assert "stop" not in ev["by_action"]  # a stop is not a model call


def test_no_stops_no_audit():
    assert evaluation.evaluate_signals([log_row("2026-11-01 08:05", p_up=0.6)])["stops"] is None


def test_calibration_split_by_cycle_kind():
    rows = []
    for d in range(1, 8):
        old = log_row(f"2026-11-{d:02d} 08:05", price=100.0 + d, p_up=0.6)  # rising: daily calls right
        rows.append(old)  # no cycle column: an older log counts as daily
        rows.append({**log_row(f"2026-11-{d:02d} 08:05", pair="ETHEUR", price=100.0 + d, p_up=0.3,
                               executed="sell"), "cycle": "extra:move"})
    by = evaluation.evaluate_signals(rows)["by_cycle"]
    assert by["daily"]["calls"] == 7 and by["daily"]["trades"] == 0 and by["daily"]["hit_rate_pct"] == 100.0
    assert by["extra"]["calls"] == 7 and by["extra"]["trades"] == 7 and by["extra"]["hit_rate_pct"] == 0.0
    lines = evaluation.evaluation_lines({"bot": {"return_pct": 0, "avg_exposure_pct": 0, "cycles": 1},
                                         **evaluation.evaluate_signals(rows)})
    assert any("extra cycles: 7 calls, 7 trades" in line for line in lines)


def test_stop_rows_are_not_counted_as_cycles(cfg):
    rows = [log_row("2026-11-01 08:05", p_up=0.6),
            log_row("2026-11-01 16:55", action="stop", executed="sell", rule="STOP (level 96)")]
    ev = evaluation.evaluation(cfg, {"eur": 1000.0, "start_eur": 1000.0, "positions": {}}, rows, {}, {})
    assert ev["bot"]["cycles"] == 1


def test_report_shows_the_api_cost(cfg, capsys):
    assert evaluation.api_cost_lines({}) == []
    state = {}
    costs.record(cfg, state, usage(), "daily", datetime(2026, 11, 6, 8, 5))
    assert evaluation.api_cost_lines(state) == ["Model API cost: 2026-11 0.08 USD (1 calls), in total 0.08 USD (1 calls)"]


# ---------------------------------------------------------------- the cycle records cost and cycle kind
def fake_markets(monkeypatch):
    daily = make_candles([100.0 + (i % 5) for i in range(60)])
    monkeypatch.setattr(market, "fetch_ohlc", lambda pair, days=60, interval=1440: (daily, 100.0))
    monkeypatch.setattr(market, "fetch_hourly", lambda pair: daily[-24:])


def test_cycle_logs_cost_and_cycle_kind(isolated, monkeypatch):
    (isolated / "config.json").write_text(json.dumps(BASE_CFG), encoding="utf-8")
    fake_markets(monkeypatch)

    def fake_claude(cfg, markets, news, portfolio, context):
        claude.last_usage = usage()
        return {pair: signal() for pair in cfg["pairs"]}, "test"

    monkeypatch.setattr(claude, "ask_claude", fake_claude)
    monkeypatch.setattr(sys, "argv", ["bot.py"])
    cli.main()

    saved = json.loads((isolated / "state.json").read_text(encoding="utf-8"))
    assert saved["api_usage"]["calls"] == 1 and saved["api_usage"]["cost_usd"] == pytest.approx(0.08)
    assert {r["cycle"] for r in journal.read_log()} == {"daily"}


def test_failed_call_is_still_counted(isolated, monkeypatch):
    (isolated / "config.json").write_text(json.dumps(BASE_CFG), encoding="utf-8")
    fake_markets(monkeypatch)

    def refused(*args):
        claude.last_usage = usage(tout=500)
        raise RuntimeError("refusal")

    monkeypatch.setattr(claude, "ask_claude", refused)
    monkeypatch.setattr(sys, "argv", ["bot.py"])
    with pytest.raises(RuntimeError):
        cli.main()
    saved = json.loads((isolated / "state.json").read_text(encoding="utf-8"))
    assert saved["api_usage"]["calls"] == 1 and journal.read_log() == []
