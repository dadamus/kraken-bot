"""Paths, endpoints and loading config.json / .env.

The state and log paths are module attributes read at call time (config.STATE_FILE), because live mode
switches them to its own files - live never mixes with paper.
"""
import json
import os
from pathlib import Path

from .i18n import t

BASE = Path(__file__).resolve().parent.parent  # the add-on folder (kraken_bot/)
# data directory: /config in the add-on / Docker image, ./data next to the add-on folder when run from the repo
DATA = Path(os.environ.get("BOT_DATA_DIR") or BASE.parent / "data")
CONFIG_FILE = DATA / "config.json"
STATE_FILE = DATA / "state.json"
LOG_FILE = DATA / "decisions.csv"
LIVE_STATE_FILE = DATA / "state_live.json"
LIVE_LOG_FILE = DATA / "decisions_live.csv"

KRAKEN = "https://api.kraken.com"
KRAKEN_FUTURES = "https://futures.kraken.com/derivatives/api/v3/tickers"
FEAR_GREED = "https://api.alternative.me/fng/"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
DERIBIT_DVOL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
UA = {"User-Agent": "kraken-bot/2.0 (+https://github.com/dadamus/kraken-bot)"}


def load_env():
    env = DATA / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config():
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if "pairs" not in cfg:  # old format: a single pair
        cfg["pairs"] = [cfg["pair"]]
    return cfg


def use_live_files():
    global STATE_FILE, LOG_FILE
    STATE_FILE, LOG_FILE = LIVE_STATE_FILE, LIVE_LOG_FILE


def mode_label(cfg, live):
    if not live:
        return "paper"
    return t("mode_live_validate") if cfg.get("live_validate_only", True) else "live"
