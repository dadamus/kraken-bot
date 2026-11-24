"""CFD paper tests: the simulator's files in a temporary directory (the root conftest already blocks the network)."""
import pytest
from .cfd_helpers import NOW

from cfd import account, cycle, futures, settings, texts


@pytest.fixture(autouse=True)
def cfd_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "cfd_config.json")
    monkeypatch.setattr(settings, "STATE_FILE", tmp_path / "cfd_state.json")
    monkeypatch.setattr(settings, "CALLS_FILE", tmp_path / "calls.csv")
    monkeypatch.setattr(settings, "TRADES_FILE", tmp_path / "trades.csv")
    monkeypatch.setattr(settings, "SHADOW_FILE", tmp_path / "shadow.csv")
    monkeypatch.setattr(settings, "LIVE_STATE_FILE", tmp_path / "state_live.json")
    monkeypatch.setattr(settings, "LIVE_CALLS_FILE", tmp_path / "calls_live.csv")
    monkeypatch.setattr(settings, "LIVE_TRADES_FILE", tmp_path / "trades_live.csv")
    monkeypatch.setattr(settings, "LIVE", False)
    monkeypatch.setattr(futures, "ALLOW", "none")  # live.prepare / smoke_test switch it on for one test only
    for var in ("CFD_EXECUTION", "KRAKEN_FUTURES_API_KEY", "KRAKEN_FUTURES_API_SECRET"):
        monkeypatch.delenv(var, raising=False)
    texts.set_language({})
    return tmp_path


@pytest.fixture
def ccfg():
    cfg = settings.defaults()
    cfg["home_assistant"]["enabled"] = False
    cfg["pairs"] = ["XBTEUR", "ETHEUR", "SOLEUR"]  # crypto only; gold and EUR/USD have their own tests
    cfg["spread_pct"] = {"XBTEUR": 0.2, "ETHEUR": 0.2, "SOLEUR": 0.2, "default": 0.2}
    return cfg


@pytest.fixture
def cstate(ccfg):
    return account.new_state(ccfg, NOW)


@pytest.fixture
def no_data(monkeypatch):
    """No news and a fixed market context for daily_cycle."""
    monkeypatch.setattr(cycle.market, "fetch_news", lambda feeds: ["headline"])
    monkeypatch.setattr(cycle, "load_context", lambda pairs, state, closed: {"fear_greed": {"today": 50}})
