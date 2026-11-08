"""CFD paper tests: the simulator's files in a temporary directory (the root conftest already blocks the network)."""
import pytest
from .cfd_helpers import NOW

from cfd import account, settings, texts


@pytest.fixture(autouse=True)
def cfd_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "cfd_config.json")
    monkeypatch.setattr(settings, "STATE_FILE", tmp_path / "cfd_state.json")
    monkeypatch.setattr(settings, "CALLS_FILE", tmp_path / "calls.csv")
    monkeypatch.setattr(settings, "TRADES_FILE", tmp_path / "trades.csv")
    texts.set_language({})
    return tmp_path


@pytest.fixture
def ccfg():
    cfg = settings.defaults()
    cfg["home_assistant"]["enabled"] = False
    cfg["spread_pct"] = {"XBTEUR": 0.2, "ETHEUR": 0.2, "SOLEUR": 0.2, "default": 0.2}
    return cfg


@pytest.fixture
def cstate(ccfg):
    return account.new_state(ccfg, NOW)

