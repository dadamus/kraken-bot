#!/usr/bin/env python3
"""
Container entry point (Home Assistant add-on or standalone Docker): runs the bot on a schedule.

- the full cycle (bot.py) once a day at run_time; if the container starts after that time and today's
  cycle has not run yet (e.g. after a restart), the cycle starts right away,
- a refresh (bot.py --ha: stop-loss / take-profit on the current price + Home Assistant entities)
  every refresh_minutes, and immediately after Home Assistant restarts.

Options come from /data/options.json in the add-on, or from environment variables in standalone Docker.
Bot data (config.json, state.json, decisions.csv) lives in /config: /addon_configs/<slug> in Home
Assistant, a mounted volume in Docker.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

APP = Path(__file__).resolve().parent
OPTIONS = Path(os.environ.get("ADDON_OPTIONS", "/data/options.json"))
DATA = Path(os.environ.get("BOT_DATA_DIR", "/config"))
LAST_RUN = DATA / ".last_run"


def env_bool(name, default=False):
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


def load_options():
    """Add-on options, or the same settings from environment variables (standalone Docker)."""
    if OPTIONS.exists():
        opts = json.loads(OPTIONS.read_text(encoding="utf-8"))
        opts["entry_prices"] = {e["pair"].upper(): e["price"]
                                for e in opts.get("entry_prices", []) if e.get("price")}
        return opts
    return {"anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "kraken_api_key": os.environ.get("KRAKEN_API_KEY", ""),
            "kraken_api_secret": os.environ.get("KRAKEN_API_SECRET", ""),
            "run_time": os.environ.get("RUN_TIME", "08:05"),
            "refresh_minutes": int(os.environ.get("REFRESH_MINUTES", "15")),
            "live": env_bool("LIVE"),
            "language": os.environ.get("BOT_LANGUAGE", "en"),
            "entry_prices": json.loads(os.environ.get("ENTRY_PRICES") or "{}")}


def ensure_config(in_home_assistant):
    """First start: copy the example configuration (paper mode) so the bot can run right away."""
    target = DATA / "config.json"
    if target.exists():
        return
    DATA.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((APP / "config.example.json").read_text(encoding="utf-8"))
    cfg["home_assistant"]["enabled"] = in_home_assistant
    target.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"Created {target} from the example (paper mode). Edit it to change pairs and risk rules.", flush=True)


def ha_lost_states():
    """After a Home Assistant restart entities set through the API disappear - refresh at once instead of
    waiting. When Home Assistant is not up yet (no answer) wait for the next minute."""
    if not os.environ.get("HA_URL") or not os.environ.get("HA_TOKEN"):
        return False
    try:
        r = requests.get(f"{os.environ['HA_URL']}/api/states/sensor.kraken_bot_equity", timeout=10,
                         headers={"Authorization": f"Bearer {os.environ['HA_TOKEN']}"})
        return r.status_code == 404
    except requests.RequestException:
        return False


def run(*args):
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] bot.py {' '.join(args)}".rstrip(), flush=True)
    subprocess.run([sys.executable, "-u", str(APP / "bot.py"), *args])


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # stop quickly when the container is stopped
    opts = load_options()
    in_ha = bool(os.environ.get("SUPERVISOR_TOKEN"))

    os.environ["BOT_DATA_DIR"] = str(DATA)
    if in_ha:  # the add-on talks to Home Assistant through the Supervisor, no token to configure
        os.environ.setdefault("HA_URL", "http://supervisor/core")
        os.environ["HA_TOKEN"] = os.environ["SUPERVISOR_TOKEN"]
    for opt, env in (("anthropic_api_key", "ANTHROPIC_API_KEY"),
                     ("kraken_api_key", "KRAKEN_API_KEY"), ("kraken_api_secret", "KRAKEN_API_SECRET")):
        if opts.get(opt):
            os.environ[env] = opts[opt]
    os.environ["BOT_LANGUAGE"] = opts.get("language") or "en"
    os.environ["BOT_ENTRY_PRICES"] = json.dumps(opts.get("entry_prices") or {})
    ensure_config(in_ha)

    cycle_args = ["--live"] if opts.get("live") else []
    refresh = int(opts.get("refresh_minutes") or opts.get("ha_refresh_minutes") or 15)
    print(f"Kraken bot: daily cycle at {opts['run_time']}, refresh every {refresh} min,"
          f" mode {'LIVE' if cycle_args else 'paper'}, language {os.environ['BOT_LANGUAGE']}", flush=True)

    run("--ha", *cycle_args)
    last_refresh = time.monotonic()
    while True:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        done = LAST_RUN.read_text().strip() if LAST_RUN.exists() else ""
        if now.strftime("%H:%M") >= opts["run_time"] and done != today:
            LAST_RUN.write_text(today)  # written before the run: a failing cycle is not retried every minute
            run(*cycle_args)
            last_refresh = time.monotonic()
        elif time.monotonic() - last_refresh >= refresh * 60 or ha_lost_states():
            run("--ha", *cycle_args)
            last_refresh = time.monotonic()
        time.sleep(60 - datetime.now().second)


if __name__ == "__main__":
    main()
