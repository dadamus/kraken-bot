"""The bot's own dashboard: a read-only web page served by the add-on (Home Assistant ingress panel).

GET /          -> panel/index.html
GET /api/data  -> everything the page shows, built from the bot's files on every request
Nothing here can trade or change settings. In Home Assistant only the ingress proxy may connect.
"""
import csv
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, macro, market, portfolio, triggers

PAGE = Path(__file__).resolve().parent / "panel" / "index.html"
INGRESS_PROXY = "172.30.32.2"  # Home Assistant's ingress proxy
LOG_ROWS = 400  # the newest decisions sent to the page
PRICE_TTL = 30  # seconds a ticker price is reused


class Prices:
    """Current prices from the public ticker, cached briefly; on failure the last known price."""

    def __init__(self):
        self.cache, self.lock = {}, threading.Lock()

    def get(self, pair, fallback=None):
        with self.lock:
            hit = self.cache.get(pair)
            if hit and time.time() - hit[0] < PRICE_TTL:
                return hit[1]
        try:
            price = market.fetch_price(pair)
        except Exception:
            return hit[1] if hit else fallback
        with self.lock:
            self.cache[pair] = (time.time(), price)
        return price


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_log(path):
    try:
        with path.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    return rows if rows and "pair" in rows[0] else []


def num(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def thresholds(cfg, state, prices, now):
    """When the next extra cycle can start: price band per pair, earliest time, today's count."""
    s = triggers.settings(cfg)
    last = state.get("last_cycle")
    done = state.get("extra_cycles") or {}
    out = {"enabled": s["enabled"], "settings": s, "last_cycle": last and last["date"],
           "today": done.get("n", 0) if done.get("date") == now.strftime("%Y-%m-%d") else 0, "pairs": {}}
    if not last:
        return out
    out["earliest"] = (datetime.strptime(last["date"], triggers.FMT)
                       + timedelta(hours=s["min_gap_hours"])).strftime(triggers.FMT)
    for pair, ref in last["prices"].items():
        atr = (last.get("atr") or {}).get(pair)
        if atr and pair in prices:
            row = {"ref": ref, "low": ref - s["move_atr"] * atr, "high": ref + s["move_atr"] * atr,
                   "low_from": "atr", "high_from": "atr", "atr": atr, "price": prices[pair],
                   "watch": (last.get("watch") or {}).get(pair)}
            w = row["watch"] or {}
            if s["watch_levels"] and w.get("below") and w["below"] > row["low"]:  # the closer level starts it
                row["low"], row["low_from"] = w["below"], "watch"
            if s["watch_levels"] and w.get("above") and w["above"] < row["high"]:
                row["high"], row["high_from"] = w["above"], "watch"
            out["pairs"][pair] = row
    return out


def build(cfg, state, log, prices, opts):
    now = datetime.now()
    pairs = cfg["pairs"]
    positions = {}
    for pair in pairs:
        pos = state.get("positions", {}).get(pair, portfolio.empty_position())
        price, entry, first = prices.get(pair), pos.get("entry_price"), state.get("first_prices", {}).get(pair)
        positions[pair] = {
            "coin": pos.get("coin", 0.0), "price": price, "value": (pos.get("coin") or 0) * (price or 0),
            "entry_price": entry, "stop_price": pos.get("stop_price"), "exchange_stop": pos.get("exchange_stop"),
            "pnl_pct": (price / entry - 1) * 100 if entry and price else None,
            "buy_and_hold_pct": (price / first - 1) * 100 if first and price else None}
    known = {p: v for p, v in prices.items() if v is not None}
    equity = portfolio.equity_of(state, known) if len(known) == len(pairs) else None
    cache = state.get("macro_calendar_cache") or {}
    keep = ("date", "pair", "price", "model_action", "confidence", "p_up_3d", "size_pct", "executed", "rule",
            "qty", "eur", "equity_eur", "reasoning", "cycle", "watch")
    return {
        "now": now.strftime(triggers.FMT), "language": opts.get("language", "en"),
        "mode": "live" if opts.get("live") else "paper", "run_time": opts.get("run_time"), "pairs": pairs,
        "portfolio": {"eur": state.get("eur"), "equity": equity, "start_eur": state.get("start_eur"),
                      "return_pct": (equity / state["start_eur"] - 1) * 100 if equity and state.get("start_eur") else None,
                      "halted_until": state.get("halted_until"), "synced_at": state.get("synced_at"),
                      "cash_flows": state.get("cash_flows") or []},
        "positions": positions, "summary": state.get("last_summary"), "charts": state.get("charts") or {},
        "evaluation": state.get("evaluation"), "api_usage": state.get("api_usage"),
        "triggers": thresholds(cfg, state, known, now), "context": state.get("last_context") or {},
        "market_overview": state.get("market_overview"),
        "calendar": macro.calendar_events(cache.get("events") or [], datetime.now(timezone.utc)),
        "risk": {k: cfg["risk"].get(k) for k in ("min_confidence", "stop_loss_pct", "take_profit_pct",
                                                 "max_exposure_pct", "max_exposure_per_pair_pct")},
        "decisions": [{k: r.get(k) for k in keep} for r in log[-LOG_ROWS:]],
    }


def data(opts, prices_cache):
    live = bool(opts.get("live"))
    cfg = read_json(config.DATA / "config.json") or {}
    if "pairs" not in cfg:
        return {"error": "no config.json yet"}
    state = read_json(config.DATA / ("state_live.json" if live else "state.json"), {}) or {}
    log = read_log(config.DATA / ("decisions_live.csv" if live else "decisions.csv"))
    last_price = {r["pair"]: num(r["price"]) for r in log}
    prices = {pair: prices_cache.get(pair, last_price.get(pair)) for pair in cfg["pairs"]}
    return build(cfg, state, log, prices, opts)


def handler(opts, only_from=None):
    prices_cache = Prices()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if only_from and self.client_address[0] != only_from:
                return self.reply(403, b"forbidden", "text/plain")
            path = self.path.split("?", 1)[0].rstrip("/")
            if path in ("", "/index.html"):
                return self.reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/data":
                try:
                    body = json.dumps(data(opts, prices_cache), ensure_ascii=False, default=str).encode("utf-8")
                except Exception as e:  # the page shows the error instead of hanging
                    body = json.dumps({"error": str(e)}).encode("utf-8")
                return self.reply(200, body, "application/json; charset=utf-8")
            return self.reply(404, b"not found", "text/plain")

        def reply(self, code, body, kind):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep the add-on log for the bot
            pass

    return Handler


def serve(opts, port, only_from=None):
    """Starts the panel in a background thread. Returns the server (or None when the port is taken)."""
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), handler(opts, only_from))
    except OSError as e:
        print(f"Panel not started on port {port}: {e}", flush=True)
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Panel on port {port}" + (" (Home Assistant ingress)" if only_from else ""), flush=True)
    return server
