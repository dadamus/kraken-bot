"""The daily cycle and the refresh end to end (data and the model replaced), the report, the panel, the model's
answer and the copies of the shared packages."""
from datetime import timedelta
from pathlib import Path

import pytest
from .intraday_helpers import FMT, NOW, breakout_up, days, hours, set_regime

from intraday import cycle, engine, journal, model, panel, report


def fake_markets(prices, daily):
    def load(pairs, offline_mode, crypto=None):
        markets = {p: {"price_now": prices[p], "atr14_pct": 3.0} for p in pairs}
        return markets, dict(prices), {p: daily for p in pairs}
    return load


@pytest.fixture
def no_data(monkeypatch):
    monkeypatch.setattr(cycle.market, "fetch_news", lambda feeds: ["headline"])
    monkeypatch.setattr(cycle.cfd_cycle, "load_context", lambda pairs, state, closed: {"fear_greed": {"today": 50}})


def test_daily_cycle_sets_both_regimes_logs_them_and_trades_at_once(icfg, istate, monkeypatch, no_data):
    up = days([100 * 1.01 ** i for i in range(80)])  # the rule says trend_up
    monkeypatch.setattr(cycle.cfd_cycle, "load_markets", fake_markets({"XBTEUR": 101.5}, up))
    seen = {}

    def ask(cfg, payload):
        seen.update(payload)
        return {"XBTEUR": {"regime": "range", "confidence": 0.7, "reasoning": "chop"}}, "summary"

    cycle.daily_cycle(icfg, istate, now=NOW, ask=ask, hours=lambda cfg: {"XBTEUR": breakout_up()})
    assert istate["regimes"]["model"]["XBTEUR"]["regime"] == "range"
    assert istate["regimes"]["rules"]["XBTEUR"]["regime"] == "trend_up"
    regimes = journal.read_regimes()
    assert [(r["book"], r["regime"]) for r in regimes] == [("model", "range"), ("rules", "trend_up")]
    # the same spike: a fade in the model book, a breakout in the rules book
    assert istate["books"]["model"]["positions"]["XBTEUR"]["strategy"] == "reversion"
    assert istate["books"]["rules"]["positions"]["XBTEUR"]["strategy"] == "breakout"
    assert seen["markets"]["XBTEUR"]["hourly"]["spread_in_hourly_atrs"] > 0
    assert "breakout" in seen["trading_terms"]["strategies"] and seen["news_last_24h"] == ["headline"]
    assert seen["fear_greed"] == {"today": 50}
    assert istate["last_hour_t"] == breakout_up()[-1]["t"]
    assert istate["evaluation"]["books"]["model"]["equity_eur"] < 1000  # the spread is paid on entry
    assert engine.load_state(icfg)["regimes"]["model"]["XBTEUR"]["regime"] == "range"


def test_a_failing_model_call_keeps_the_rules_regime(icfg, istate, monkeypatch, no_data):
    up = days([100 * 1.01 ** i for i in range(80)])
    monkeypatch.setattr(cycle.cfd_cycle, "load_markets", fake_markets({"XBTEUR": 101.5}, up))

    def ask(cfg, payload):
        raise RuntimeError("API down")

    with pytest.raises(RuntimeError):
        cycle.daily_cycle(icfg, istate, now=NOW, ask=ask, hours=lambda cfg: {"XBTEUR": breakout_up()})
    assert engine.load_state(icfg)["regimes"]["rules"]["XBTEUR"]["regime"] == "trend_up"


def test_refresh_runs_the_hourly_step_once_per_new_candle(icfg, istate, monkeypatch):
    monkeypatch.setattr(cycle, "fetch_prices", lambda cfg, state: {"XBTEUR": 101.5})
    set_regime(istate, "trend_up", book="rules", confidence=None)
    calls = []

    def loader(cfg):
        calls.append(1)
        return {"XBTEUR": breakout_up()}

    istate["last_hour_t"] = breakout_up()[-1]["t"]  # this candle was processed already
    cycle.refresh(icfg, istate, now=NOW, fetch=None, hours=loader)
    assert calls == [] and istate["books"]["rules"]["positions"] == {}
    istate["last_hour_t"] = breakout_up()[-2]["t"]
    cycle.refresh(icfg, istate, now=NOW, fetch=None, hours=loader)
    assert calls == [1] and "XBTEUR" in istate["books"]["rules"]["positions"]
    assert istate["last_step"]["books"]["model"]["XBTEUR"] == "no regime yet"
    assert istate["books"]["rules"]["equity_by_day"]["2026-11-04"] < 1000


def test_stops_of_both_books_need_one_candle_request_per_pair(icfg, istate, monkeypatch):
    monkeypatch.setattr(cycle, "fetch_prices", lambda cfg, state: {"XBTEUR": 101.5})
    for name, opened in (("model", NOW - timedelta(hours=3)), ("rules", NOW - timedelta(hours=1))):
        set_regime(istate, "trend_up", book=name)
        engine.step(icfg, istate, name, {"XBTEUR": hours([c["c"] for c in breakout_up()], now=opened)},
                    {"XBTEUR": 101.5}, opened)
    requests = []

    def fetch(pair, since, now):
        requests.append((pair, since))
        low = istate["books"]["model"]["positions"]["XBTEUR"]["stop"] - 1
        return [{"t": int((NOW - timedelta(minutes=30)).timestamp()), "o": 101, "h": 101, "l": low, "c": 100}], 1

    istate["last_hour_t"] = int(NOW.timestamp())  # no hourly step in this refresh
    cycle.refresh(icfg, istate, now=NOW, fetch=fetch, hours=lambda cfg: {})
    assert requests == [("XBTEUR", int((NOW - timedelta(hours=3)).timestamp()))]
    closed = journal.read_trades()
    assert sorted(r["book"] for r in closed) == ["model", "rules"] and {r["reason"] for r in closed} == {"STOP"}
    assert {r["strategy"] for r in closed} == {"breakout"}


def test_report_days_and_regime_settlement():
    d = report.days({"2026-11-01": 1010.0, "2026-11-02": 1005.0, "2026-11-03": 1005.0, "2026-11-04": 1020.0},
                    1000.0, today="2026-11-04")
    assert (d["n"], d["green"], d["red"], d["flat"], d["today_eur"]) == (3, 1, 1, 1, 15.0)
    rows = [{"date": "2026-11-01 07:45", "book": "model", "pair": "XBTEUR", "price": "100", "regime": "trend_up",
             "atr_pct": "3"},
            {"date": "2026-11-02 07:45", "book": "model", "pair": "XBTEUR", "price": "102", "regime": "range",
             "atr_pct": "3"},
            {"date": "2026-11-03 07:45", "book": "model", "pair": "XBTEUR", "price": "101", "regime": "range",
             "atr_pct": "3"},
            {"date": "2026-11-06 07:45", "book": "model", "pair": "XBTEUR", "price": "90", "regime": "range",
             "atr_pct": "3"}]  # three days later: not settled
    s = report.settle_regimes(rows)["model"]
    assert s["trend_up"] == {"n": 1, "avg_move_pct": 2.0, "avg_abs_move_atr": 0.67, "hit_rate_pct": 100.0}
    assert s["range"]["n"] == 1 and s["range"]["hit_rate_pct"] == 100.0  # -0.98 % within one 3 % ATR


def test_stats_separate_the_costs():
    rows = [{"net_eur": "-1", "spread_eur": "1.5", "swap_eur": "0.2", "r_multiple": "-0.2", "hours": "3",
             "reason": "TIME"}]
    s = report.stats(rows)
    assert s["gross_eur"] == 0.7 and s["net_eur"] == -1.0 and s["by_reason"] == {"TIME": 1}


def test_model_answer_is_validated(icfg):
    icfg["pairs"] = ["XBTEUR", "PAXGEUR", "EURUSD"]
    regimes, summary = model.parse_answer(icfg, {"regimes": [
        {"pair": "xbteur", "regime": "trend_up", "confidence": 1.7, "reasoning": "r"},
        {"pair": "PAXGEUR", "regime": "moon", "confidence": 0.6, "reasoning": "r"}], "summary": "s"})
    assert regimes["XBTEUR"] == {"regime": "trend_up", "confidence": 1.0, "reasoning": "r"}
    assert regimes["PAXGEUR"]["regime"] == "no_trade"
    assert regimes["EURUSD"]["regime"] == "no_trade" and regimes["EURUSD"]["confidence"] == 0.0
    assert "{LANGUAGE}" not in model.system_prompt() and "English" in model.system_prompt()
    assert set(model.schema(icfg["pairs"])["properties"]["regimes"]["items"]["properties"]["regime"]["enum"]) == \
        {"trend_up", "trend_down", "range", "no_trade"}


def test_panel_renders_books_regimes_and_the_backtest(icfg, istate):
    set_regime(istate, "trend_up")
    set_regime(istate, "range", book="rules", confidence=None)
    engine.step(icfg, istate, "model", {"XBTEUR": breakout_up()}, {"XBTEUR": 101.5}, NOW)
    istate["last_prices"] = {"XBTEUR": 101.5}
    istate["evaluation"] = report.evaluation(icfg, istate, {"XBTEUR": 101.5}, [], [], NOW)
    bt = {"date": NOW.strftime(FMT), "from": "2026-10-05 12:00", "to": "2026-11-04 12:00", "return_pct": 1.5,
          "hold_return_pct": -2.0, "trades": {"all": {"n": 3, "net_eur": 15.0, "gross_eur": 20.0}},
          "days": {"green": 4, "n": 9}, "by_pair_strategy": {"XBTEUR breakout": report.stats([])}}
    html = panel.render(icfg, istate, [], {"run_time": "07:45"}, bt)
    assert "Intraday paper" in html and "trend_up (0.80)" in html and ">range<" in html
    assert "breakout" in html and "-2.0% holding" in html and "XBTEUR breakout" in html
    assert "No backtest yet" in panel.render(icfg, istate, [], {"run_time": "07:45"}, None)


def test_offline_daily_cycle_runs_end_to_end(icfg, istate):
    icfg["pairs"] = ["XBTEUR", "PAXGEUR", "EURUSD"]
    state = engine.new_state(icfg, NOW)
    cycle.daily_cycle(icfg, state, offline_mode=True, now=NOW)
    assert set(state["regimes"]["rules"]) == {"XBTEUR", "PAXGEUR", "EURUSD"}
    assert set(state["last_step"]["books"]["model"]) == {"XBTEUR", "PAXGEUR", "EURUSD"}


ROOT = Path(__file__).resolve().parents[2]


def files(base):
    return {p.relative_to(base).as_posix(): p for p in base.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def text(p):
    return p.read_bytes().replace(b"\r\n", b"\n")  # a Windows checkout may differ in line endings only


@pytest.mark.parametrize("source, copy", [("kraken_bot/krakenbot", "kraken_intraday/krakenbot"),
                                          ("kraken_cfd/cfd", "kraken_intraday/cfd")])
def test_the_shared_copies_match(source, copy):
    src, dst = files(ROOT / source), files(ROOT / copy)
    assert sorted(src) == sorted(dst), "file list differs - run: python scripts/sync_krakenbot.py"
    changed = [name for name in src if text(src[name]) != text(dst[name])]
    assert not changed, f"{changed} differ - run: python scripts/sync_krakenbot.py"
