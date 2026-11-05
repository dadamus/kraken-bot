"""Languages. Everything the user reads (logs, Home Assistant names, notifications, the model's reasoning)
comes from locales/<lang>.toml; adding a language means adding a file with the same keys as en.toml.

Data written to the log stays machine-readable: "model: hold", "STOP", "TAKE-PROFIT" and "STOP-LOSS"
are the same in every language.
"""
import os
import tomllib
from pathlib import Path

LOCALES = Path(__file__).resolve().parent / "locales"
DEFAULT = "en"


def _load():
    return {p.stem: tomllib.loads(p.read_text(encoding="utf-8")) for p in sorted(LOCALES.glob("*.toml"))}


CATALOGS = _load()
LANGUAGES = {code: cat["language_name"] for code, cat in CATALOGS.items()}  # code -> English name
LANG = DEFAULT


def set_language(cfg=None):
    """Language from the BOT_LANGUAGE variable (add-on option), then config.json "language", default English."""
    global LANG
    lang = (os.environ.get("BOT_LANGUAGE") or (cfg or {}).get("language") or DEFAULT).lower()
    LANG = lang if lang in CATALOGS else DEFAULT
    return LANG


def language_name():
    return LANGUAGES[LANG]


def t(key, **kw):
    """The message `key` in the current language (English when the translation is missing)."""
    text = CATALOGS[LANG].get(key)
    if text is None:
        text = CATALOGS[DEFAULT][key]
    return text.format(**kw)
