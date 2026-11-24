#!/usr/bin/env python3
"""Container entry point (Home Assistant add-on or standalone Docker): runs the simulator on a schedule.

- the daily cycle (cfd_bot.py) at run_time; after a restart past run_time it runs at once if today's has not,
- a refresh (cfd_bot.py --refresh: stops / take-profits replayed on minute candles, overnight swap, margin
  stop-out, Home Assistant) every refresh_minutes,
- the dashboard page on port 8098 (ingress in Home Assistant),
- an alert to the phone when a cycle fails, or refreshes fail three times in a row,
- with live_smoke_test switched on: one minimum-size round trip on Kraken Derivatives at start (real money,
  see cfd/live.py smoke_test), once per switch-on.

Execution (option execution): paper, shadow (the orders Kraken would need, checked, never sent) or live (real
orders; also needs "mode": "live" in config.json - two opt-ins).

Options come from /data/options.json in the add-on, or from environment variables in Docker. The data
(config.json, state.json, calls.csv, trades.csv) lives in /config: /addon_configs/<id>_kraken_cfd in Home
Assistant.
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

APP = Path(__file__).resolve().parent
OPTIONS = Path(os.environ.get("ADDON_OPTIONS", "/data/options.json"))
DATA = Path(os.environ.get("CFD_DATA_DIR", "/config"))
LAST_RUN = DATA / ".last_run"
SMOKE_DONE = DATA / ".smoke_test"
os.environ["CFD_DATA_DIR"] = str(DATA)

from cfd import panel, settings, texts  # noqa: E402  (after CFD_DATA_DIR is set)
from krakenbot import alerts  # noqa: E402


def load_options():
    if OPTIONS.exists():
        return json.loads(OPTIONS.read_text(encoding="utf-8"))
    return {"anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "language": os.environ.get("BOT_LANGUAGE", "en"),
            "run_time": os.environ.get("RUN_TIME", "08:20"),
            "refresh_minutes": int(os.environ.get("REFRESH_MINUTES", "5")),
            "execution": os.environ.get("CFD_EXECUTION", "paper"),
            "kraken_futures_api_key": os.environ.get("KRAKEN_FUTURES_API_KEY", ""),
            "kraken_futures_api_secret": os.environ.get("KRAKEN_FUTURES_API_SECRET", ""),
            "live_smoke_test": os.environ.get("LIVE_SMOKE_TEST", "").lower() in ("1", "true", "yes")}


def ensure_config(in_home_assistant, language):
    target = settings.CONFIG_FILE
    if target.exists():
        return
    DATA.mkdir(parents=True, exist_ok=True)
    shutil.copy(settings.EXAMPLE, target)
    cfg = json.loads(target.read_text(encoding="utf-8"))
    cfg["home_assistant"]["enabled"] = in_home_assistant
    cfg["language"] = language
    target.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"Created {target} from the example (1000 virtual EUR, leverage 2). Edit it to change pairs, costs"
          " and risk rules.", flush=True)


def run(*args):
    """Runs cfd_bot.py; its output goes to the add-on log. Returns (exit code, last error line)."""
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] cfd_bot.py {' '.join(args)}".rstrip(), flush=True)
    done = subprocess.run([sys.executable, "-u", str(APP / "cfd_bot.py"), *args], stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace")
    if done.stderr:
        sys.stderr.write(done.stderr)
        sys.stderr.flush()
    lines = [x.strip() for x in (done.stderr or "").splitlines() if x.strip()]
    return done.returncode, (lines[-1][:300] if lines else "")


def alert(message):
    try:
        cfg = settings.load_config()
    except Exception:
        cfg = {}
    texts.set_language(cfg)
    alerts.send(cfg, texts.t("alert_title"), message)


def smoke_test(opts):
    """The one-off live test, once per switch-on of the option (switching it off forgets the last run)."""
    if not opts.get("live_smoke_test"):
        SMOKE_DONE.unlink(missing_ok=True)
        return
    if SMOKE_DONE.exists():
        print(f"Live smoke test already ran ({SMOKE_DONE.read_text().strip()}); switch the option off and on to"
              " repeat it.", flush=True)
        return
    code, err = run("--live-smoke-test")
    result = "PASSED" if code == 0 else "FAILED"
    SMOKE_DONE.write_text(f"{datetime.now():%Y-%m-%d %H:%M} {result}")
    alert(f"live smoke test on Kraken Derivatives: {result}" + (f" ({err})" if code and err else "")
          + " - details in the add-on log")


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    opts = load_options()
    in_ha = bool(os.environ.get("SUPERVISOR_TOKEN"))
    if in_ha:  # the add-on talks to Home Assistant through the Supervisor
        os.environ.setdefault("HA_URL", "http://supervisor/core")
        os.environ["HA_TOKEN"] = os.environ["SUPERVISOR_TOKEN"]
    if opts.get("anthropic_api_key"):
        os.environ["ANTHROPIC_API_KEY"] = opts["anthropic_api_key"]
    os.environ["BOT_LANGUAGE"] = opts.get("language") or "en"
    os.environ["CFD_EXECUTION"] = opts.get("execution") or "paper"
    for opt, env in (("kraken_futures_api_key", "KRAKEN_FUTURES_API_KEY"),
                     ("kraken_futures_api_secret", "KRAKEN_FUTURES_API_SECRET")):
        if opts.get(opt):
            os.environ[env] = opts[opt]
    ensure_config(in_ha, os.environ["BOT_LANGUAGE"])
    panel.serve(opts, int(os.environ.get("PANEL_PORT", "8098")), panel.INGRESS_PROXY if in_ha else None)

    refresh = int(opts.get("refresh_minutes") or 5)
    mode = os.environ["CFD_EXECUTION"]
    note = {"paper": "PAPER ONLY", "shadow": "paper + shadow orders on Kraken (none sent)",
            "live": "LIVE on Kraken Derivatives if config.json has \"mode\": \"live\""}.get(mode, mode)
    print(f"CFD: daily cycle at {opts['run_time']}, refresh every {refresh} min, {note}", flush=True)
    smoke_test(opts)
    failed = 0
    last_refresh = 0.0
    while True:
        today = datetime.now().strftime("%Y-%m-%d")
        done = LAST_RUN.read_text().strip() if LAST_RUN.exists() else ""
        if datetime.now().strftime("%H:%M") >= opts["run_time"] and done != today:
            LAST_RUN.write_text(today)  # before the run: a failing cycle is not retried every minute
            code, err = run()
            if code:
                alert(f"the daily cycle failed (exit {code}): {err or '-'}")
            last_refresh = time.monotonic()
        elif time.monotonic() - last_refresh >= refresh * 60 or not last_refresh:
            code, err = run("--refresh")
            last_refresh = time.monotonic()
            failed = failed + 1 if code else 0
            if failed == 3:
                alert(f"the refresh failed 3 times in a row: {err or '-'}")
        time.sleep(60 - datetime.now().second)


if __name__ == "__main__":
    main()
