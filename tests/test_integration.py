"""Whole bot runs (offline, --ha, --live) and the container entry point (addon.py)."""
import json
import random
import sys

import pytest

import addon
from krakenbot import claude, cli, journal, market
from conftest import BASE_CFG, make_state


def run_main(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["bot.py", *args])
    return cli.main()


def write_config(isolated, **overrides):
    (isolated / "config.json").write_text(json.dumps({**BASE_CFG, **overrides}), encoding="utf-8")


def test_offline_cycle_logs_every_pair(isolated, monkeypatch, capsys):
    write_config(isolated)
    random.seed(7)
    run_main(monkeypatch, "--offline")

    rows = journal.read_log()
    assert sorted(r["pair"] for r in rows) == sorted(BASE_CFG["pairs"])
    state = json.loads((isolated / "state.json").read_text(encoding="utf-8"))
    assert "evaluation" in state and state["evaluation"]["bot"]["cycles"] == 1
    for r in rows:  # every decision within the rules
        assert r["executed"] in ("buy", "sell", "none")
        assert 0 <= float(r["p_up_3d"]) <= 1
    assert "mode: PAPER" in capsys.readouterr().out


def test_offline_cycle_in_polish(isolated, monkeypatch, capsys):
    write_config(isolated, language="pl")
    random.seed(7)
    run_main(monkeypatch, "--offline")
    out = capsys.readouterr().out
    assert "tryb: PAPER" in out and "kapitał:" in out
    assert all(r["reasoning"].startswith("TEST OFFLINE") for r in journal.read_log())


def test_language_option_overrides_config(isolated, monkeypatch, capsys):
    write_config(isolated, language="pl")
    monkeypatch.setenv("BOT_LANGUAGE", "en")  # the add-on option wins
    run_main(monkeypatch, "--offline")
    assert "mode: PAPER" in capsys.readouterr().out


def test_ha_refresh_runs_stop_without_model(isolated, monkeypatch):
    write_config(isolated)
    state = make_state(eur=0, XBTEUR={"coin": 1.0, "entry_price": 100.0, "stop_price": 95.0})
    (isolated / "state.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(market, "fetch_price", lambda pair: {"XBTEUR": 94.0, "ETHEUR": 2000.0, "SOLEUR": 80.0}[pair])
    monkeypatch.setattr(claude, "ask_claude", lambda *a: pytest.fail("--ha must never ask the model"))
    run_main(monkeypatch, "--ha")

    rows = journal.read_log()
    assert [(r["pair"], r["model_action"], r["executed"]) for r in rows] == [("XBTEUR", "stop", "sell")]
    saved = json.loads((isolated / "state.json").read_text(encoding="utf-8"))
    assert saved["positions"]["XBTEUR"]["coin"] == 0 and saved["eur"] > 90


def test_ha_refresh_without_trigger_changes_nothing(isolated, monkeypatch):
    write_config(isolated)
    monkeypatch.setattr(market, "fetch_price", lambda pair: 100.0)
    run_main(monkeypatch, "--ha")
    assert journal.read_log() == [] and not (isolated / "state.json").exists()


def test_live_requires_live_mode_in_config(isolated, monkeypatch):
    write_config(isolated, mode="paper")
    with pytest.raises(SystemExit, match="mode"):
        run_main(monkeypatch, "--live")


# ---------------------------------------------------------------- addon.py
@pytest.fixture
def container(isolated, monkeypatch):
    monkeypatch.setattr(addon, "OPTIONS", isolated / "options.json")
    monkeypatch.setattr(addon, "DATA", isolated / "config")
    for name in ("RUN_TIME", "REFRESH_MINUTES", "LIVE", "ENTRY_PRICES", "HA_URL", "HA_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return isolated


def test_standalone_options_from_environment(container, monkeypatch):
    monkeypatch.setenv("RUN_TIME", "07:30")
    monkeypatch.setenv("REFRESH_MINUTES", "5")
    monkeypatch.setenv("LIVE", "true")
    monkeypatch.setenv("BOT_LANGUAGE", "pl")
    monkeypatch.setenv("ENTRY_PRICES", '{"XBTEUR": 65617}')
    opts = addon.load_options()
    assert (opts["run_time"], opts["refresh_minutes"], opts["live"], opts["language"]) == ("07:30", 5, True, "pl")
    assert opts["entry_prices"] == {"XBTEUR": 65617}


def test_addon_options_file(container):
    (container / "options.json").write_text(json.dumps({
        "run_time": "08:05", "ha_refresh_minutes": 15, "live": False, "language": "en",
        "entry_prices": [{"pair": "xbteur", "price": 65617}, {"pair": "SOLEUR", "price": 0}]}), encoding="utf-8")
    opts = addon.load_options()
    assert opts["entry_prices"] == {"XBTEUR": 65617}  # 0 = unknown, skipped


@pytest.mark.parametrize("in_ha", [True, False])
def test_first_start_creates_paper_config(container, in_ha):
    addon.ensure_config(in_ha)
    cfg = json.loads((container / "config" / "config.json").read_text(encoding="utf-8"))
    assert cfg["mode"] == "paper" and cfg["live_validate_only"] is True
    assert cfg["home_assistant"]["enabled"] is in_ha  # standalone Docker has no Home Assistant by default


def test_existing_config_is_never_overwritten(container):
    (container / "config").mkdir()
    (container / "config" / "config.json").write_text('{"mode": "live"}', encoding="utf-8")
    addon.ensure_config(True)
    assert json.loads((container / "config" / "config.json").read_text(encoding="utf-8")) == {"mode": "live"}


def test_no_home_assistant_check_without_ha(container):
    assert addon.ha_lost_states() is False  # no HA_URL / HA_TOKEN -> no request at all


# ---------------------------------------------------------------- addon.py: stop watcher between refreshes
def watcher_files(container, cfg, positions, live=False, **state_extra):
    data = container / "config"
    data.mkdir(exist_ok=True)
    (data / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    state = {"eur": 0, "positions": positions, **state_extra}
    (data / ("state_live.json" if live else "state.json")).write_text(json.dumps(state), encoding="utf-8")


def test_watcher_finds_breached_stop(container, cfg, monkeypatch):
    watcher_files(container, cfg, {"XBTEUR": {"coin": 1.0, "entry_price": 100.0, "stop_price": 95.0},
                                   "ETHEUR": {"coin": 0.0, "entry_price": None}})
    asked = []
    monkeypatch.setattr(market, "fetch_price", lambda pair: asked.append(pair) or 94.0)
    assert addon.stop_hits(live=False) == ["XBTEUR 94"]
    assert asked == ["XBTEUR"]  # pairs without coins are not even priced


def test_watcher_quiet_above_stop_and_for_dust(container, cfg, monkeypatch):
    watcher_files(container, cfg, {"XBTEUR": {"coin": 1.0, "entry_price": 100.0, "stop_price": 95.0},
                                   "ETHEUR": {"coin": 0.00001, "entry_price": 100.0, "stop_price": 95.0}})
    monkeypatch.setattr(market, "fetch_price", lambda pair: 96.0 if pair == "XBTEUR" else 50.0)
    assert addon.stop_hits(live=False) == []


def test_watcher_skips_pair_already_fired_in_validation(container, cfg, monkeypatch):
    cfg = {**cfg, "mode": "live"}
    today = addon.datetime.now().strftime("%Y-%m-%d")
    watcher_files(container, cfg, {"XBTEUR": {"coin": 1.0, "entry_price": 100.0, "stop_price": 95.0}},
                  live=True, guard_fired={"XBTEUR": today})
    monkeypatch.setattr(market, "fetch_price", lambda pair: 90.0)
    assert addon.stop_hits(live=True) == []


def test_watcher_survives_missing_files_and_network(container, cfg, monkeypatch):
    assert addon.stop_hits(live=False) == []  # no config / state yet
    watcher_files(container, cfg, {"XBTEUR": {"coin": 1.0, "entry_price": 100.0, "stop_price": 95.0}})

    def down(pair):
        raise ConnectionError("no network")
    monkeypatch.setattr(market, "fetch_price", down)
    assert addon.stop_hits(live=False) == []
