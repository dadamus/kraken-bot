"""The add-on's dashboard: one read-only page rendered from the files on every request (Home Assistant
ingress panel). Prices are the ones of the last refresh; nothing here can trade or change settings."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cfd import account
from cfd.panel import CSS, esc, signed, table, td

from . import backtest, engine, journal, settings, texts
from .texts import t

INGRESS_PROXY = "172.30.32.2"  # Home Assistant's ingress proxy
EXTRA_CSS = ".trend_up{color:var(--up);font-weight:600}.trend_down{color:var(--down);font-weight:600}" \
            ".range{color:var(--accent);font-weight:600}.no_trade{color:var(--mute)}"


def book_name(name):
    return t(f"book_{name}")


def regime_cell(r):
    if not r:
        return td("-")
    conf = f" ({r['confidence']:.2f})" if r.get("confidence") is not None else ""
    return td(f"{esc(r['regime'])}{esc(conf)}", r["regime"])


def stats_rows(ev):
    rows = []
    for name, b in (ev.get("books") or {}).items():
        for strategy, s in b["trades"]["by_strategy"].items():
            rows.append([td(esc(book_name(name))), td(esc(strategy)), td(s["n"]), td(esc(s["win_rate_pct"])),
                         td(signed(s["net_eur"], " EUR")), td(signed(s["gross_eur"], " EUR")), td(esc(s["avg_r"])),
                         td(esc(s["avg_hours"]))])
    return rows


def render(cfg, state, trades, opts, bt=None):
    prices = state.get("last_prices") or {}
    model_book, rules_book = state["books"]["model"], state["books"]["rules"]
    eq = account.equity(cfg, model_book, prices)
    eq_rules = account.equity(cfg, rules_book, prices)
    ev = state.get("evaluation") or {}
    mb = (ev.get("books") or {}).get("model") or {}
    days = mb.get("days") or {}
    bench = mb.get("benchmark") or {}
    costs = model_book.get("costs") or {}
    usage = state.get("api_usage") or {}
    tiles = [(t("equity"), f"{eq:,.2f} EUR"),
             (t("return"), signed((eq / model_book["start_eur"] - 1) * 100, "%")),
             (t("rules_return"), signed((eq_rules / rules_book["start_eur"] - 1) * 100, "%")),
             (t("vs_hold"), signed(bench.get("vs_hold_pp"), " pp") if bench else "-"),
             (t("today"), signed(days.get("today_eur"), " EUR") if days.get("today_eur") is not None else "-"),
             (t("green_days"), f"{days['green']} / {days['n']}" if days.get("n") else "-"),
             (t("costs"), f"{costs.get('spread_eur', 0):,.2f} {t('spread')} + {costs.get('swap_eur', 0):,.2f} {t('swap')}"),
             (t("model_cost"), f"{usage['cost_usd']:.2f} USD" if usage else "-")]
    out = [f"<h1>{esc(t('title'))}</h1><p class='sub'>{esc(t('subtitle'))} · {esc(t('updated'))}"
           f" {esc(state.get('last_refresh') or '-')}</p>",
           '<div class="tiles">' + "".join(f'<div class="tile"><span>{esc(k)}</span><b>{v}</b></div>'
                                          for k, v in tiles) + "</div>"]
    if model_book.get("halted_until"):
        out.append(f'<div class="note">{esc(t("halted", until=model_book["halted_until"]))}</div>')
    if state.get("last_summary"):
        out.append(f'<div class="note"><b>{esc(t("summary"))}:</b> {esc(state["last_summary"])}</div>')

    step = state.get("last_step") or {}
    steps = step.get("books") or {}
    regimes = state.get("regimes") or {}
    rows = [[td(esc(pair)), regime_cell(regimes.get("model", {}).get(pair)), regime_cell(regimes.get("rules", {}).get(pair)),
             td(esc((steps.get("model") or {}).get(pair, "-")), "wrap"),
             td(esc((regimes.get("model", {}).get(pair) or {}).get("reasoning", "")), "wrap")]
            for pair in cfg["pairs"]] if regimes.get("model") or regimes.get("rules") else []
    out.append(f"<h2>{esc(t('regimes'))}</h2>")
    out.append(table([t("pair"), t("model"), t("rule"), f"{t('last_step')} {step.get('date', '')}", t("reasoning")],
                     rows, t("no_regimes", run_time=opts.get("run_time", "-"))))

    rows = []
    for name in engine.BOOKS:
        for pair, pos in state["books"][name]["positions"].items():
            price = account.price_of(prices, pair, pos)
            rows.append([td(esc(book_name(name))), td(esc(pair)), td(esc(pos.get("strategy"))),
                         td(esc(pos["side"]), pos["side"]), td(f"{pos['entry']:,.6g}"), td(f"{price:,.6g}"),
                         td(f"{pos['stop']:,.6g}"), td(f"{pos['take_profit']:,.6g}"),
                         td(signed(account.unrealized(cfg, pair, pos, price, prices), " EUR")), td(esc(pos["opened"]))])
    out.append(f"<h2>{esc(t('positions'))}</h2>")
    out.append(table([t("book"), t("pair"), t("strategy"), t("side"), t("entry"), t("price"), t("stop"),
                      t("take_profit"), t("unrealized"), t("opened")], rows, t("no_positions")))

    rows = [[td(esc(r["closed"])), td(esc(book_name(r.get("book") or "model"))), td(esc(r["pair"])),
             td(esc(r.get("strategy"))), td(esc(r["side"]), r["side"]), td(esc(r["reason"])),
             td(signed(r["move_pct"], "%")), td(signed(r["net_eur"], " EUR")), td(esc(r["r_multiple"])),
             td(esc(r.get("hours")))] for r in reversed(trades[-80:])]
    out.append(f"<h2>{esc(t('trades'))}</h2>")
    out.append(table([t("closed"), t("book"), t("pair"), t("strategy"), t("side"), t("reason"), t("move"), t("net"),
                      "R", t("hours")], rows, t("no_trades")))

    if ev:
        out.append(f"<h2>{esc(t('stats'))}</h2>")
        out.append(table([t("book"), t("strategy"), t("n"), t("win_rate") + " %", t("net"), t("gross"), t("avg_r"),
                          t("hours")], stats_rows(ev), t("no_trades")))
        by_day = {}
        for name in engine.BOOKS:
            for r in ((ev["books"].get(name) or {}).get("days") or {}).get("last") or []:
                by_day.setdefault(r["date"], {})[name] = r["pnl_eur"]
        rows = [[td(esc(d)), td(signed(v.get("model"), " EUR")), td(signed(v.get("rules"), " EUR"))]
                for d, v in sorted(by_day.items(), reverse=True)[:14]]
        out.append(f"<h2>{esc(t('days'))}</h2>")
        out.append(table([t("date"), book_name("model"), book_name("rules")], rows, t("no_days")))
        rows = [[td(esc(book_name(name))), td(esc(r), r), td(s["n"]), td(signed(s["avg_move_pct"], "%")),
                 td(esc(s["avg_abs_move_atr"])), td(esc(s["hit_rate_pct"] if s["hit_rate_pct"] is not None else "-"))]
                for name, regs in (ev.get("regimes") or {}).items() for r, s in sorted(regs.items())]
        out.append(f"<h2>{esc(t('regime_settle'))}</h2>")
        out.append(table([t("book"), t("regime"), t("n"), t("avg_move"), t("in_atr"), t("right") + " %"], rows, "-"))

    out.append(f"<h2>{esc(t('backtest'))}</h2>")
    if bt:
        a, d = bt["trades"]["all"], bt["days"]
        out.append(f'<div class="note">{esc(t("backtest_line", start=bt["from"], end=bt["to"], ret=bt["return_pct"], hold=bt["hold_return_pct"], n=a["n"], net=a["net_eur"], gross=a["gross_eur"], green=d["green"], days=d["n"], date=bt["date"]))}</div>')
        rows = [[td(esc(key)), td(s["n"]), td(esc(s["win_rate_pct"])), td(signed(s["net_eur"], " EUR")),
                 td(signed(s["gross_eur"], " EUR")), td(esc(s["avg_r"])), td(esc(s["avg_hours"]))]
                for key, s in bt["by_pair_strategy"].items()]
        out.append(table([t("strategy"), t("n"), t("win_rate") + " %", t("net"), t("gross"), t("avg_r"), t("hours")],
                         rows, t("no_trades")))
    else:
        out.append(f'<div class="box"><div class="empty">{esc(t("backtest_none"))}</div></div>')
    return (f"<!doctype html><html lang='{texts.LANG}'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'><meta http-equiv='refresh' content='60'>"
            f"<title>Intraday paper</title><style>{CSS}{EXTRA_CSS}</style></head><body><main>{''.join(out)}</main></body></html>")


def page(opts):
    cfg = settings.load_config()
    texts.set_language(cfg)
    state = engine.load_state(cfg)
    return render(cfg, state, journal.read_trades(), opts, backtest.load_result())


def handler(opts, only_from=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if only_from and self.client_address[0] != only_from:
                return self.send_error(403)
            try:
                if self.path.split("?")[0].endswith("/api/state"):
                    body, ctype = json.dumps(engine.load_state(settings.load_config())).encode(), "application/json"
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
