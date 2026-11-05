"""Settling the model's calls, forecast calibration and benchmark strategies."""
from datetime import datetime

import pytest

from krakenbot import evaluation
from conftest import make_candles, make_state, log_row

START = datetime(2026, 1, 5, 8, 0).timestamp()  # local time, as in the log


def day(d, hour_shift=0.0):
    return datetime.fromtimestamp(START + d * 86400 + hour_shift * 3600).strftime("%Y-%m-%d %H:%M")


def daily_log(n=10, growth=1.01, p_up=0.7):
    """A cycle every day at 8:00, the price rises 1% a day; even days buy, odd days hold."""
    return [log_row(day(d), price=100 * growth ** d, action="buy" if d % 2 == 0 else "hold", p_up=p_up)
            for d in range(n)]


def test_price_after_uses_nearest_point_within_tolerance():
    series = [(0, 100.0), (86400 + 3600, 101.0), (3 * 86400, 103.0)]
    assert evaluation.price_after(series, 0, 1) == 101.0  # one hour off - within tolerance
    assert evaluation.price_after(series, 0, 3) == 103.0
    assert evaluation.price_after(series, 0, 7) is None  # 7 days have not passed yet


def test_returns_by_action_and_calibration():
    rows = daily_log()
    rows.append(log_row(day(5, 6.5), action="stop", executed="sell", rule="STOP (level 99)", price=104))
    ev = evaluation.evaluate_signals(rows)

    buy = ev["by_action"]["buy"]
    assert buy["signals"] == 5
    assert buy["settled_3d"] == 4  # days 0, 2, 4, 6 already have a 3-day result
    assert buy["avg_1d_pct"] == pytest.approx(1.0)
    assert buy["avg_3d_pct"] == pytest.approx((1.01 ** 3 - 1) * 100, abs=0.01)
    assert buy["up_3d_pct"] == 100.0
    assert "stop" not in ev["by_action"]  # automatic sales are not model calls

    cal = ev["p_up_3d"]
    assert cal["settled"] == 7
    assert cal["brier"] == pytest.approx(0.09)  # (0.7 - 1)^2
    assert cal["brier_skill_pct"] == pytest.approx(64.0)
    assert cal["hit_rate_pct"] == 100.0
    assert cal["buckets"] == [{"p_range": "0.65-1.00", "n": 7, "avg_p": 0.7, "actual_up_pct": 100.0}]


def test_wrong_forecasts_score_badly():
    cal = evaluation.evaluate_signals(daily_log(p_up=0.3))["p_up_3d"]
    assert cal["hit_rate_pct"] == 0.0
    assert cal["brier"] == pytest.approx(0.49)
    assert cal["brier_skill_pct"] < 0  # worse than a coin flip


def test_coin_flip_forecasts_have_no_direction():
    cal = evaluation.evaluate_signals(daily_log(p_up=0.5))["p_up_3d"]
    assert cal["hit_rate_pct"] is None  # 0.5 means no view - it does not count towards the hit rate
    assert cal["brier"] == pytest.approx(0.25)


def test_rows_without_forecast_are_skipped():
    ev = evaluation.evaluate_signals(daily_log(p_up=None))
    assert ev["p_up_3d"]["settled"] == 0 and ev["p_up_3d"]["brier"] is None


def test_candles_fill_gaps_in_log():
    """When the bot did not run for a few days, the "after 3 days" price comes from a Kraken candle."""
    # candle i closes (t + 1 day) exactly at 8:00 on day i, at price 100 + i
    candles = make_candles([100.0 + i for i in range(20)], start=int(START) - 86400)
    rows = [log_row(day(0), price=100.0)]
    without = evaluation.evaluate_signals(rows)
    with_candles = evaluation.evaluate_signals(rows, {"XBTEUR": candles})
    assert without["by_action"]["hold"]["settled_3d"] == 0
    assert with_candles["by_action"]["hold"]["avg_3d_pct"] == pytest.approx(3.0)


def shadow_setup(closes):
    candles = make_candles(closes)
    first = candles[80]
    start_local = datetime.fromtimestamp(first["t"] + 86400 + 3600).strftime("%Y-%m-%d %H:%M")
    rows = [log_row(start_local, price=first["c"])]
    return rows, {"XBTEUR": candles}, first["c"]


def test_trend_strategy_follows_uptrend(cfg):
    closes = [100 * 1.01 ** i for i in range(100)]
    rows, candles, p0 = shadow_setup(closes)
    now = closes[-1] * 1.01
    s = evaluation.shadow_strategies(cfg, rows, candles, {"XBTEUR": now})
    cost = 0.005
    assert s["buy_and_hold_pct"] == pytest.approx((now / p0 * (1 - cost) - 1) * 100, abs=0.01)
    assert s["trend_pct"] == pytest.approx(s["buy_and_hold_pct"], abs=0.01)  # in the market the whole time
    assert s["per_pair"]["XBTEUR"]["trend_in_market"] is True
    assert s["trend_rule"] == "close > SMA50"


def test_trend_strategy_sits_out_downtrend(cfg):
    closes = [100 * 0.99 ** i for i in range(100)]
    rows, candles, _ = shadow_setup(closes)
    s = evaluation.shadow_strategies(cfg, rows, candles, {"XBTEUR": closes[-1] * 0.99})
    assert s["trend_pct"] == 0.0 and s["per_pair"]["XBTEUR"]["trend_in_market"] is False
    assert s["buy_and_hold_pct"] < -10


def test_shadow_needs_enough_history(cfg):
    rows, candles, _ = shadow_setup([100.0] * 100)
    assert evaluation.shadow_strategies(cfg, rows, {"XBTEUR": candles["XBTEUR"][:30]}, {"XBTEUR": 100.0}) is None


def test_full_evaluation_and_report_lines(cfg):
    rows = [dict(r, eur=500.0, equity_eur=1000.0) for r in daily_log(4)]
    state = make_state(eur=500, XBTEUR={"coin": 5.0, "entry_price": 100})
    ev = evaluation.evaluation(cfg, state, rows, {}, {"XBTEUR": 110.0, "ETHEUR": 1, "SOLEUR": 1})

    assert ev["bot"]["return_pct"] == pytest.approx(5.0)  # 500 + 5 * 110 = 1050
    assert ev["bot"]["avg_exposure_pct"] == 50.0
    assert ev["bot"]["cycles"] == 4
    assert "shadow" not in ev  # no candles, no benchmark strategies
    lines = "\n".join(evaluation.evaluation_lines(ev))
    assert "Bot: +5.00%" in lines and "Note" in lines
