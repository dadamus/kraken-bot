"""The add-on's dashboard: one read-only page rendered from the files on every request (Home Assistant
ingress panel). Prices are the ones of the last refresh; nothing here can trade or change settings."""
import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import account, journal, settings, texts
from .texts import t

INGRESS_PROXY = "172.30.32.2"  # Home Assistant's ingress proxy

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--mute:#677086;--line:#e3e6ec;--up:#14804a;--down:#c2352b;--accent:#3b5bdb}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--card:#1a1e24;--ink:#e6e9ef;--mute:#98a1b3;--line:#2a3039;--up:#4cc38a;--down:#ff6b5f;--accent:#8ea2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0}h2{font-size:15px;margin:22px 0 8px}
.sub{color:var(--mute);margin:2px 0 14px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.tile b{display:block;font-size:19px;margin-top:2px}.tile span{color:var(--mute);font-size:12px}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}
th{color:var(--mute);font-weight:600}td.wrap{white-space:normal;min-width:260px}tr:last-child td{border-bottom:0}
.up{color:var(--up)}.down{color:var(--down)}.long{color:var(--up);font-weight:600}.short{color:var(--down);font-weight:600}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:8px;padding:10px 12px;margin-top:10px}
.empty{color:var(--mute);padding:12px}
"""


def esc(x):
    return html.escape("" if x is None else str(x))


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def signed(x, unit=""):
    v = num(x)
    if v is None:
        return "-"
    cls = "up" if v > 0 else "down" if v < 0 else ""
    return f'<span class="{cls}">{v:+,.2f}{unit}</span>'


def table(headers, rows, empty):
    if not rows:
        return f'<div class="box"><div class="empty">{esc(empty)}</div></div>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(rows_cell for rows_cell in r) + "</tr>" for r in rows)
    return f'<div class="box"><table><tr>{head}</tr>{body}</table></div>'


def td(x, cls=""):
    return f'<td class="{cls}">{x}</td>'


def render(cfg, state, calls, trades, opts):
    prices = state.get("last_prices") or {}
    eq = account.equity(cfg, state, prices)
    level = account.margin_level(cfg, state, prices)
    ev = state.get("evaluation") or {}
    bench = ev.get("benchmark") or {}
    costs = state.get("costs") or {}
    usage = state.get("api_usage") or {}
    tiles = [(t("equity"), f"{eq:,.2f} EUR"),
             (t("return"), signed((eq / state["start_eur"] - 1) * 100, "%")),
             (t("vs_hold"), signed(bench.get("vs_hold_pp"), " pp") if bench else "-"),
             (t("balance"), f"{state['balance']:,.2f} EUR"),
             (t("margin"), f"{account.margin_used(cfg, state, prices):,.2f} EUR"),
             (t("margin_level"), f"{level:,.0f}%" if level else "-"),
             (t("costs"), f"{costs.get('spread_eur', 0):,.2f} {t('spread')} + {costs.get('swap_eur', 0):,.2f} {t('swap')}"),
             (t("model_cost"), f"{usage['cost_usd']:.2f} USD" if usage else "-")]
    out = [f"<h1>{esc(t('title'))}</h1><p class='sub'>{esc(t('subtitle'))} · {esc(t('updated'))}"
           f" {esc(state.get('last_refresh') or '-')}</p>",
           '<div class="tiles">' + "".join(f'<div class="tile"><span>{esc(k)}</span><b>{v}</b></div>'
                                          for k, v in tiles) + "</div>"]
    if state.get("halted_until"):
        out.append(f'<div class="note">{esc(t("halted", until=state["halted_until"]))}</div>')
    if state.get("last_summary"):
        out.append(f'<div class="note"><b>{esc(t("summary"))}:</b> {esc(state["last_summary"])}</div>')

    rows = []
    for pair, pos in state["positions"].items():
        price = account.price_of(prices, pair, pos)
        rows.append([td(esc(pair)), td(esc(pos["side"]), pos["side"]), td(f"{pos['qty']:.6g}"),
                     td(f"{pos['entry']:,.6g}"), td(f"{price:,.6g}"), td(f"{pos['stop']:,.6g}"),
                     td(f"{pos['take_profit']:,.6g}"),
                     td(signed(account.unrealized(cfg, pair, pos, price), " EUR")),
                     td(f"{pos['swap_eur']:.2f}"), td(esc(pos["opened"]))])
    out.append(f"<h2>{esc(t('positions'))}</h2>")
    out.append(table([t("pair"), t("side"), t("qty"), t("entry"), t("price"), t("stop"), t("take_profit"),
                      t("unrealized"), t("swap"), t("opened")], rows, t("no_positions")))

    rows = [[td(esc(r["date"])), td(esc(r["pair"])), td(esc(r["action"]), r["action"]),
             td(esc(r["confidence"])), td(esc(r["p_up_3d"])), td(esc(r["executed"])),
             td(esc(r["rule"]), "wrap"), td(esc(r["reasoning"]), "wrap")] for r in reversed(calls[-60:])]
    out.append(f"<h2>{esc(t('calls'))}</h2>")
    out.append(table([t("date"), t("pair"), t("action"), t("confidence"), "p_up_3d", t("executed"), t("rule"),
                      t("reasoning")], rows, t("no_calls", run_time=opts.get("run_time", "-"))))

    rows = [[td(esc(r["closed"])), td(esc(r["pair"])), td(esc(r["side"]), r["side"]), td(esc(r["reason"])),
             td(signed(r["move_pct"], "%")), td(signed(r["net_eur"], " EUR")), td(esc(r["r_multiple"])),
             td(esc(r["swap_eur"])), td(esc(r["spread_eur"])), td(esc(r["days"]))] for r in reversed(trades[-60:])]
    out.append(f"<h2>{esc(t('trades'))}</h2>")
    out.append(table([t("closed"), t("pair"), t("side"), t("reason"), t("move"), t("net"), "R", t("swap"),
                      t("spread"), t("days")], rows, t("no_trades")))

    if ev:
        out.append(f"<h2>{esc(t('stats'))}</h2>")
        tr = ev["trades"]
        rows = [[td(esc(side)), td(tr[side]["n"]), td(esc(tr[side]["win_rate_pct"])),
                 td(signed(tr[side]["net_eur"], " EUR")), td(esc(tr[side]["avg_r"]))] for side in ("long", "short", "all")]
        out.append(table([t("by_side"), t("n"), t("win_rate") + " %", t("net"), t("avg_r")], rows, ""))
        by = ev["calls"]["by_action"]
        rows = [[td(esc(a)), td(by[a]["n"]), td(signed(by[a].get("avg_move_in_direction_pct"), "%")),
                 td(esc(by[a].get("hit_rate_pct", "-")))] for a in ("long", "short", "flat", "hold") if a in by]
        out.append(f"<h2>{esc(t('calls_settled'))}</h2>")
        out.append(table([t("action"), t("n"), t("avg_move"), t("hit_rate") + " %"], rows, "-"))
        p = ev["calls"]["p_up_3d"]
        out.append(f'<div class="note">{esc(t("brier"))}: <b>{esc(p["brier"] if p["brier"] is not None else "-")}</b>'
                   f' (n={p["n"]}), {esc(t("hit_rate"))} {esc(p["hit_rate_pct"] if p["hit_rate_pct"] is not None else "-")}%</div>')
    return (f"<!doctype html><html lang='{texts.LANG}'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'><meta http-equiv='refresh' content='60'>"
            f"<title>CFD paper</title><style>{CSS}</style></head><body><main>{''.join(out)}</main></body></html>")


def page(opts):
    cfg = settings.load_config()
    texts.set_language(cfg)
    state = account.load_state(cfg)
    return render(cfg, state, journal.read_calls(), journal.read_trades(), opts)


def handler(opts, only_from=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if only_from and self.client_address[0] != only_from:
                return self.send_error(403)
            try:
                if self.path.split("?")[0].endswith("/api/state"):
                    body, ctype = json.dumps(account.load_state(settings.load_config())).encode(), "application/json"
                else:
                    body, ctype = page(opts).encode("utf-8"), "text/html; charset=utf-8"
            except Exception as e:  # the page must not take the add-on down
                body, ctype = f"error: {e}".encode(), "text/plain; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


def serve(opts, port, only_from=None):
    server = ThreadingHTTPServer(("0.0.0.0", port), handler(opts, only_from))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
