"""Shared test setup: every test gets its own directory for state and log, and the network is
blocked - no test can accidentally call Kraken or Claude."""
import copy
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "kraken_bot"))

import bot  # noqa: E402

BASE_CFG = {
    "mode": "paper",
    "pairs": ["XBTEUR", "ETHEUR", "SOLEUR"],
    "model": "claude-opus-5-5",
    "paper_start_eur": 1000,
    "fee_pct": 0.4,
    "slippage_pct": 0.1,
    "live_validate_only": True,
    "risk": {
        "min_confidence": 0.65,
        "max_trade_eur": 200,
        "min_trade_eur": 10,
        "max_exposure_pct": 60,
        "max_exposure_per_pair_pct": 30,
        "stop_loss_pct": 8,
        "take_profit_pct": 15,
        "max_drawdown_pct": 20,
        "halt_days": 7,
    },
    "news_feeds": [],
    "home_assistant": {"enabled": False},
}

MONDAY = int(datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp())  # a Monday, 00:00 UTC


@pytest.fixture
def cfg():
    return copy.deepcopy(BASE_CFG)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Bot files in a temporary directory, no network, no waiting, English messages."""
    monkeypatch.setattr(bot, "DATA", tmp_path)
    monkeypatch.setattr(bot, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bot, "LOG_FILE", tmp_path / "decisions.csv")
    monkeypatch.setattr(bot, "LIVE_STATE_FILE", tmp_path / "state_live.json")
    monkeypatch.setattr(bot, "LIVE_LOG_FILE", tmp_path / "decisions_live.csv")

    def no_network(*args, **kwargs):
        raise AssertionError(f"the test tried to use the network: {args[:1]}")

    monkeypatch.setattr(bot.requests, "get", no_network)
    monkeypatch.setattr(bot.requests, "post", no_network)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.delenv("BOT_ENTRY_PRICES", raising=False)
    monkeypatch.delenv("BOT_LANGUAGE", raising=False)
    bot.set_language({})
    return tmp_path


def make_candles(closes, start=MONDAY, spread=1.0, volume=1000.0):
    """Daily candles like fetch_ohlc returns: high/low = close ± spread, open = previous close."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append({"t": start + i * 86400, "o": prev, "h": c + spread, "l": c - spread, "c": c, "v": volume})
        prev = c
    return out


def make_state(eur=1000.0, start_eur=1000.0, **positions):
    """positions: XBTEUR=dict(coin=..., entry_price=..., stop_price=...)."""
    state = {"eur": eur, "start_eur": start_eur, "positions": {}, "first_prices": {}, "halted_until": None}
    for pair in BASE_CFG["pairs"]:
        state["positions"][pair] = {"coin": 0.0, "entry_price": None, "stop_price": None}
    for pair, pos in positions.items():
        state["positions"][pair].update(pos)
    return state


def log_row(date, pair="XBTEUR", price=100.0, action="hold", confidence=0.5, p_up=None,
            executed="none", rule="model: hold", eur=1000.0, equity=1000.0):
    return {"date": date, "pair": pair, "price": price, "model_action": action, "confidence": confidence,
            "p_up_3d": p_up, "size_pct": 0, "executed": executed, "rule": rule, "qty": 0, "eur": eur,
            "coin": 0, "equity_eur": equity, "reasoning": "test"}


def signal(action="hold", confidence=0.8, size_pct=50, p_up=0.5, stop=None):
    return {"action": action, "confidence": confidence, "size_pct": size_pct, "p_up_3d": p_up,
            "stop_price": stop, "reasoning": "test"}
