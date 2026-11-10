"""Intraday paper tests: the simulator's files in a temporary directory (the root conftest already blocks the
network)."""
from datetime import timedelta

import pytest
from .intraday_helpers import NOW

from intraday import engine, settings, texts


@pytest.fixture(autouse=True)
def intraday_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "intraday_config.json")
    monkeypatch.setattr(settings, "STATE_FILE", tmp_path / "intraday_state.json")
    monkeypatch.setattr(settings, "REGIMES_FILE", tmp_path / "regimes.csv")
    monkeypatch.setattr(settings, "TRADES_FILE", tmp_path / "trades.csv")
    monkeypatch.setattr(settings, "BACKTEST_FILE", tmp_path / "backtest.json")
    texts.set_language({})
    return tmp_path


@pytest.fixture
def icfg():
    cfg = settings.defaults()
    cfg["home_assistant"]["enabled"] = False
    cfg["pairs"] = ["XBTEUR"]  # one 24/7 instrument; gold and EUR/USD where their hours matter
    cfg["spread_pct"]["XBTEUR"] = 0.1
    return cfg


@pytest.fixture
def istate(icfg):
    return engine.new_state(icfg, NOW - timedelta(days=1))
