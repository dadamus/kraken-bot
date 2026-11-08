"""The rules in code, the stop replay between cycles, the model's answer and the evaluation."""
from datetime import datetime, timedelta

import pytest
from .cfd_helpers import NOW, sig

from cfd import account, guard, model, report, risk

PRICES = {"XBTEUR": 50000.0, "ETHEUR": 3000.0, "SOLEUR": 150.0}


# --- stop and take-profit placement -------------------------------------------------------------------

def test_default_stop_is_two_atr_and_take_profit_two_r(ccfg):
    stop, tp, dist = risk.levels(ccfg, "long", 100.0, 3.0)
    assert (stop, tp, dist) == (94.0, 112.0, 6.0)
    stop, tp, _ = risk.levels(ccfg, "short", 100.0, 3.0)
    assert (stop, tp) == (106.0, 88.0)


def test_model_stop_is_kept_between_one_atr_and_max_stop_pct(ccfg):
    assert risk.levels(ccfg, "long", 100.0, 3.0, model_stop=99.0)[0] == 97.0  # too tight -> 1 ATR
    assert risk.levels(ccfg, "long", 100.0, 3.0, model_stop=80.0)[0] == 90.0  # too far -> 10 %
    assert risk.levels(ccfg, "short", 100.0, 3.0, model_stop=104.0)[0] == 104.0
    assert risk.levels(ccfg, "short", 100.0, 3.0, model_stop=95.0)[0] == 106.0  # wrong side -> default


def test_take_profit_closer_than_the_stop_is_replaced(ccfg):
    assert risk.levels(ccfg, "long", 100.0, 3.0, model_stop=95.0, model_tp=103.0)[1] == 110.0
    assert risk.levels(ccfg, "long", 100.0, 3.0, model_stop=95.0, model_tp=108.0)[1] == 108.0


# --- position size ------------------------------------------------------------------------------------

def test_size_risks_the_configured_share_of_equity(ccfg, cstate):
    qty, why = risk.size(ccfg, cstate, PRICES, 100.0, 5.0)
    assert why is None
    assert qty == pytest.approx(3.0)  # 1.5 % of 1000 EUR / 5 EUR distance


def test_size_is_capped_by_the_margin_per_trade(ccfg, cstate):
    qty, _ = risk.size(ccfg, cstate, PRICES, 100.0, 0.5)  # 30 coins by risk, 3000 EUR notional
    assert qty * 100 == pytest.approx(500)  # 25 % of equity as margin x leverage 2


def test_size_respects_the_total_margin_and_the_minimum(ccfg, cstate):
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.024, 50000, 48000, 54000, NOW)  # 600 EUR margin
    qty, why = risk.size(ccfg, cstate, PRICES, 100.0, 5.0)
    assert qty == 0 and "min_trade_eur" in why


# --- decisions ----------------------------------------------------------------------------------------

def test_low_confidence_does_not_open(ccfg, cstate):
    steps = risk.decide(ccfg, cstate, "XBTEUR", 50000, sig("short", 0.6), 1500, None)
    assert [s["do"] for s in steps] == ["none"]


def test_opposite_call_closes_even_with_low_confidence(ccfg, cstate):
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 47000, 56000, NOW)
    steps = risk.decide(ccfg, cstate, "XBTEUR", 50000, sig("short", 0.5), 1500, None)
    assert [s["do"] for s in steps] == ["close", "none"]


def test_reversal_closes_then_opens(ccfg, cstate):
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 47000, 56000, NOW)
    steps = risk.decide(ccfg, cstate, "XBTEUR", 50000, sig("short", 0.8), 1500, None)
    assert [s["do"] for s in steps] == ["close", "open"]
    assert steps[1]["side"] == "short" and steps[1]["stop"] > 50000


def test_shorts_can_be_switched_off(ccfg, cstate):
    ccfg["allow_short"] = False
    steps = risk.decide(ccfg, cstate, "XBTEUR", 50000, sig("short", 0.9), 1500, None)
    assert [s["do"] for s in steps] == ["none"] and "allow_short" in steps[0]["why"]


def test_halt_blocks_new_positions_but_not_closing(ccfg, cstate):
    assert risk.decide(ccfg, cstate, "XBTEUR", 50000, sig("long", 0.9), 1500, "halt")[0]["do"] == "none"
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 47000, 56000, NOW)
    assert risk.decide(ccfg, cstate, "XBTEUR", 50000, sig("flat", 0.3), 1500, "halt")[0]["do"] == "close"


def test_hold_moves_the_stop_only_closer(ccfg, cstate):
    pos = account.open_position(ccfg, cstate, "XBTEUR", "short", 0.01, 50000, 53000, 44000, NOW)
    risk.decide(ccfg, cstate, "XBTEUR", 49000, sig("hold", stop=54000), 1500, None)
    assert pos["stop"] == 53000
    risk.decide(ccfg, cstate, "XBTEUR", 49000, sig("hold", stop=51000), 1500, None)
    assert pos["stop"] == 51000


def test_drawdown_halt(ccfg, cstate):
    cstate["peak_equity"] = 1400.0  # equity 1000 = -28.6 %
    why = risk.check_halt(ccfg, cstate, PRICES, NOW)
    assert "drawdown" in why
    assert risk.check_halt(ccfg, cstate, PRICES, NOW + timedelta(days=1)).startswith("drawdown halt until")
    assert risk.check_halt(ccfg, cstate, PRICES, NOW + timedelta(days=8)) is None


# --- stops between cycles -----------------------------------------------------------------------------

def candle(t, o, h, low, c):
    return {"t": t, "o": o, "h": h, "l": low, "c": c, "v": 1}


def test_a_wick_through_the_stop_closes_the_long_at_the_stop(ccfg, cstate):
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 48000, 55000, NOW)
    t0 = int(NOW.timestamp())
    candles = [candle(t0 + 60, 50000, 50100, 49000, 49500), candle(t0 + 120, 49500, 49600, 47900, 49400)]
    rows = guard.check(ccfg, cstate, {"XBTEUR": 49400}, NOW + timedelta(minutes=5), lambda *a: (candles, 1))
    assert rows[0]["reason"] == "STOP"
    assert rows[0]["exit"] == pytest.approx(48000 * 0.999)
    assert "XBTEUR" not in cstate["positions"]


def test_stop_and_take_profit_in_one_candle_counts_as_the_stop(ccfg, cstate):
    account.open_position(ccfg, cstate, "ETHEUR", "short", 1, 3000, 3100, 2900, NOW)
    t0 = int(NOW.timestamp())
    rows = guard.check(ccfg, cstate, {}, NOW + timedelta(minutes=5),
                       lambda *a: ([candle(t0 + 60, 3000, 3150, 2850, 3000)], 1))
    assert rows[0]["reason"] == "STOP"


def test_a_gap_fills_at_the_open_not_at_the_stop(ccfg, cstate):
    account.open_position(ccfg, cstate, "ETHEUR", "short", 1, 3000, 3100, 2900, NOW)
    t0 = int(NOW.timestamp())
    rows = guard.check(ccfg, cstate, {}, NOW + timedelta(hours=2),
                       lambda *a: ([candle(t0 + 3600, 3300, 3350, 3250, 3300)], 1))
    assert rows[0]["exit"] == pytest.approx(3300 * 1.001)


def test_candles_before_the_last_check_are_not_replayed(ccfg, cstate):
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 48000, 55000, NOW)
    t0 = int(NOW.timestamp())
    cstate["last_check"] = t0 + 600
    old = candle(t0 + 60, 50000, 50000, 47000, 50000)  # before the last check
    rows = guard.check(ccfg, cstate, {"XBTEUR": 50000}, NOW + timedelta(minutes=15), lambda *a: ([old], 1))
    assert rows == []


def test_without_candles_the_current_price_still_protects(ccfg, cstate):
    account.open_position(ccfg, cstate, "XBTEUR", "long", 0.01, 50000, 48000, 55000, NOW)

    def broken(*a):
        raise RuntimeError("Kraken down")

    rows = guard.check(ccfg, cstate, {"XBTEUR": 56000}, NOW + timedelta(hours=1), broken)
    assert rows[0]["reason"] == "TAKE-PROFIT"


# --- the model's answer -------------------------------------------------------------------------------

def test_answer_is_parsed_and_sanitised(ccfg):
    signals, summary = model.parse_answer(ccfg, {"summary": "ok", "decisions": [
        {"pair": "XBTEUR", "action": "short", "confidence": 1.7, "p_up_3d": 0.4, "stop_price": 52000,
         "take_profit": None, "reasoning": "r"},
        {"pair": "ETHEUR", "action": "buy", "confidence": 0.9, "p_up_3d": "x", "stop_price": -1,
         "take_profit": 1, "reasoning": "r"}]})
    assert signals["XBTEUR"]["action"] == "short" and signals["XBTEUR"]["confidence"] == 1.0
    assert signals["ETHEUR"]["action"] == "hold" and signals["ETHEUR"]["stop_price"] is None
    assert signals["ETHEUR"]["p_up_3d"] is None
    assert signals["SOLEUR"]["confidence"] == 0.0  # skipped by the model -> hold
    assert summary == "ok"


def test_schema_lists_the_pairs_and_the_four_actions(ccfg):
    d = model.schema(ccfg["pairs"])["properties"]["decisions"]["items"]["properties"]
    assert d["pair"]["enum"] == ccfg["pairs"]
    assert d["action"]["enum"] == ["long", "short", "flat", "hold"]


# --- evaluation ---------------------------------------------------------------------------------------

def test_calls_are_settled_three_days_later(ccfg):
    day = int(datetime(2026, 11, 1).timestamp())
    candles = [{"t": day + i * 86400, "o": 100, "h": 100, "l": 100, "c": 100 - i * 2, "v": 1} for i in range(6)]
    calls = [{"date": "2026-11-01 08:00", "pair": "XBTEUR", "price": "100", "action": "short", "p_up_3d": "0.3"},
             {"date": "2026-11-01 08:00", "pair": "ETHEUR", "price": "100", "action": "long", "p_up_3d": "0.6"},
             {"date": "2026-11-05 08:00", "pair": "XBTEUR", "price": "92", "action": "long", "p_up_3d": "0.6"}]
    out = report.settle_calls(calls, {"XBTEUR": candles, "ETHEUR": candles})
    assert out["by_action"]["short"] == {"n": 1, "avg_move_pct": -6.0, "avg_move_in_direction_pct": 6.0,
                                         "hit_rate_pct": 100.0}
    assert out["by_action"]["long"]["hit_rate_pct"] == 0.0  # the 2026-11-05 call is not 3 days old yet
    assert out["p_up_3d"] == {"n": 2, "brier": round((0.3 ** 2 + 0.6 ** 2) / 2, 4), "hit_rate_pct": 50.0}


def test_trade_stats_split_by_side():
    trades = [{"side": "long", "net_eur": "10", "r_multiple": "1", "days": "1", "swap_eur": "0.5",
               "spread_eur": "1", "reason": "TAKE-PROFIT"},
              {"side": "short", "net_eur": "-5", "r_multiple": "-0.5", "days": "2", "swap_eur": "1",
               "spread_eur": "1", "reason": "STOP"}]
    s = report.trade_stats(trades)
    assert s["long"]["win_rate_pct"] == 100.0 and s["short"]["net_eur"] == -5.0
    assert s["all"]["n"] == 2 and s["all"]["by_reason"] == {"STOP": 1, "TAKE-PROFIT": 1}


def test_style_changes_the_prompt_and_unknown_falls_back_to_cautious(ccfg):
    assert "{STYLE}" not in model.system_prompt(ccfg) and "{LANGUAGE}" not in model.system_prompt(ccfg)
    assert "Prefer no trade over a weak one" in model.system_prompt(ccfg)
    ccfg["style"] = "aggressive"
    assert "Trade actively" in model.system_prompt(ccfg)
    assert "Prefer no trade" not in model.system_prompt(ccfg)
    ccfg["style"] = "yolo"
    assert model.style(ccfg) == "cautious"
