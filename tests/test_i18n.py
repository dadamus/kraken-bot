"""Languages: every locale file has the same keys and placeholders as English."""
import string

import pytest

from krakenbot import claude, i18n, risk


def fields(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


OTHER_LANGUAGES = sorted(set(i18n.CATALOGS) - {i18n.DEFAULT})


def test_english_and_polish_are_available():
    assert {"en", "pl"} <= set(i18n.LANGUAGES)
    assert i18n.LANGUAGES["pl"] == "Polish"


@pytest.mark.parametrize("lang", OTHER_LANGUAGES)
def test_locale_has_the_same_keys_as_english(lang):
    en, other = i18n.CATALOGS[i18n.DEFAULT], i18n.CATALOGS[lang]
    assert sorted(set(en) - set(other)) == [], f"missing in {lang}.toml"
    assert sorted(set(other) - set(en)) == [], f"unknown keys in {lang}.toml"


@pytest.mark.parametrize("lang", OTHER_LANGUAGES)
@pytest.mark.parametrize("key", sorted(i18n.CATALOGS[i18n.DEFAULT]))
def test_placeholders_match_english(lang, key):
    assert fields(i18n.CATALOGS[lang].get(key, "")) == fields(i18n.CATALOGS[i18n.DEFAULT][key]), key


def test_missing_translation_falls_back_to_english(monkeypatch):
    monkeypatch.delitem(i18n.CATALOGS["pl"], "why_sell")
    i18n.set_language({"language": "pl"})
    assert i18n.t("why_sell") == "model signal"


def test_machine_readable_rules_are_the_same_in_every_language(cfg):
    pos = {"coin": 1, "entry_price": 100, "stop_price": None}
    for lang in i18n.LANGUAGES:
        cfg["language"] = lang
        i18n.set_language(cfg)
        assert i18n.t("why_hold") == "model: hold"  # the dashboards and the log rely on this
        assert risk.protective_exit(cfg, pos, 90.0)["why"] == "STOP-LOSS"
        assert risk.protective_exit(cfg, pos, 120.0)["why"] == "TAKE-PROFIT"
        assert risk.protective_exit(cfg, dict(pos, stop_price=95), 94.0)["why"].startswith("STOP (")


def test_language_selection(monkeypatch):
    assert i18n.set_language({}) == "en"
    assert i18n.set_language({"language": "PL"}) == "pl"
    assert i18n.set_language({"language": "de"}) == "en"  # unknown -> English
    monkeypatch.setenv("BOT_LANGUAGE", "pl")
    assert i18n.set_language({"language": "en"}) == "pl"  # the add-on option wins over config.json


def test_system_prompt_asks_for_selected_language():
    i18n.set_language({"language": "pl"})
    assert 'Write "reasoning", "watch.condition" and "summary" in Polish.' in claude.system_prompt()
    assert "{LANGUAGE}" not in claude.system_prompt()
