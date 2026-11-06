"""Alerts to the phone (Home Assistant notify service, home_assistant.notify in config.json) when the bot
needs attention: a failed cycle, refreshes failing in a row, a stop that cannot rest on Kraken.

Problems found inside a bot run are kept in state["problems"] ({key: message}); flush() sends a new one at
once, repeats it at most every REPEAT_HOURS and says when it is gone. The add-on sends its own alerts
(failed runs) directly with send().
"""
from datetime import datetime, timedelta

from . import homeassistant
from .i18n import t

REPEAT_HOURS = 6
FMT = "%Y-%m-%d %H:%M"


def send(cfg, title, message):
    """Push notification through Home Assistant; always printed to the log too. Never raises."""
    print(f"  [alert] {title}: {message}", flush=True)
    ha = homeassistant.ha_conf(cfg)
    if not ha or not ha.get("notify"):
        return False
    try:
        domain, service = ha["notify"].split(".", 1)
        homeassistant.ha_post(ha, f"services/{domain}/{service}", {"title": title, "message": message})
        return True
    except Exception as e:
        print(t("ha_error", err=e))
        return False


def problem(state, key, message=None):
    """Marks a problem (message) or clears it (None)."""
    probs = state.setdefault("problems", {})
    if message:
        probs[key] = message
    else:
        probs.pop(key, None)


def flush(cfg, state, now=None):
    """Sends new and repeating problems and the resolution of solved ones. Returns True when it sent."""
    now = now or datetime.now()
    probs, sent = state.get("problems") or {}, state.setdefault("alerts_sent", {})
    did = False
    for key, message in probs.items():
        last = sent.get(key)
        if not last or now - datetime.strptime(last["at"], FMT) >= timedelta(hours=REPEAT_HOURS):
            send(cfg, t("alert_title"), message)
            sent[key], did = {"at": now.strftime(FMT), "message": message}, True
    for key in [k for k in sent if k not in probs]:
        send(cfg, t("alert_resolved_title"), t("alert_resolved", what=sent.pop(key)["message"]))
        did = True
    return did


class RunWatch:
    """Add-on side: counts failed refreshes in a row and alerts on failed cycles."""

    def __init__(self, load_cfg, after=3):
        self.load_cfg, self.after, self.failed, self.alerted = load_cfg, after, 0, False

    def cfg(self):
        try:
            return self.load_cfg()
        except Exception:
            return {}

    def cycle(self, code, err):
        if code:
            send(self.cfg(), t("alert_title"), t("alert_cycle_failed", code=code, err=err or "-"))

    def refresh(self, code, err):
        if code:
            self.failed += 1
            if self.failed == self.after:
                send(self.cfg(), t("alert_title"), t("alert_refresh_failing", n=self.failed, err=err or "-"))
                self.alerted = True
            return
        if self.alerted:
            send(self.cfg(), t("alert_resolved_title"), t("alert_refresh_ok"))
        self.failed, self.alerted = 0, False
