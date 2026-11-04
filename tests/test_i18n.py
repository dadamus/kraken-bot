"""Languages: every message exists in English and Polish with the same placeholders."""
import string

import pytest

import bot


def fields(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@pytest.mark.parametrize("key", sorted(bot.MESSAGES))
def test_every_message_has_all_languages_and_same_placeholders(key):
    entry = bot.MESSAGES[key]
    assert set(entry) == set(bot.LANGUAGES), key
    assert fields(entry["en"]) == fields(entry["pl"]), key


def test_machine_readable_rules_are_the_same_in_every_language(cfg):
    pos = {"coin": 1, "entry_price": 100, "stop_price": None}
    for lang in bot.LANGUAGES:
        cfg["language"] = lang
        bot.set_language(cfg)
        assert bot.t("why_hold") == "model: hold"  # the dashboards and the log rely on this
        assert bot.protective_exit(cfg, pos, 90.0)["why"] == "STOP-LOSS"
        assert bot.protective_exit(cfg, pos, 120.0)["why"] == "TAKE-PROFIT"
        assert bot.protective_exit(cfg, dict(pos, stop_price=95), 94.0)["why"].startswith("STOP (")


def test_language_selection(monkeypatch):
    assert bot.set_language({}) == "en"
    assert bot.set_language({"language": "PL"}) == "pl"
    assert bot.set_language({"language": "de"}) == "en"  # unknown -> English
    monkeypatch.setenv("BOT_LANGUAGE", "pl")
    assert bot.set_language({"language": "en"}) == "pl"  # the add-on option wins over config.json


def test_system_prompt_asks_for_selected_language():
    bot.set_language({"language": "pl"})
    assert 'Write "reasoning" and "summary" in Polish.' in bot.system_prompt()
    assert "{LANGUAGE}" not in bot.system_prompt()
