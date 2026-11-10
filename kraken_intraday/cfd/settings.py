"""Paths and config.json. The paths are module attributes read at call time (tests point them elsewhere).

config.json is merged over config.example.json, so a key added in a new version gets its default without
editing the file.
"""
import copy
import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # the add-on folder (kraken_cfd/)
# /config in the add-on image, ./data_cfd at the repository root when run from the repository
DATA = Path(os.environ.get("CFD_DATA_DIR") or BASE.parent / "data_cfd")
EXAMPLE = BASE / "config.example.json"
CONFIG_FILE = DATA / "config.json"
STATE_FILE = DATA / "state.json"
CALLS_FILE = DATA / "calls.csv"
TRADES_FILE = DATA / "trades.csv"


def merge(base, over):
    out = copy.deepcopy(base)
    for key, value in over.items():
        out[key] = merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def defaults():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def load_config():
    user = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    return merge(defaults(), user)
