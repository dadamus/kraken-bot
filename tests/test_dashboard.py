"""Home Assistant dashboard templates, rendered like HA does (needs jinja2 and pyyaml, skipped without them).
The ApexCharts data generators run in Node when it is installed."""
import json
import re
import shutil
import subprocess

import pytest

from conftest import ROOT

jinja_sandbox = pytest.importorskip("jinja2.sandbox")
yaml = pytest.importorskip("yaml")

FULL = {
    "sensor.kraken_bot_equity": ("244.29", {"mode": "live", "updated": "2026-11-04 19:15", "halted_until": None}),
    "sensor.kraken_bot_xbteur": ("231.23", {"coin": 0.00322323, "price": 71737.4, "entry_price": 65617.0,
                                            "stop_price": 67900.0, "position_pnl_pct": 9.33, "buy_and_hold_pct": 3.43}),
    "sensor.kraken_bot_etheur": ("13.06", {"coin": 0.0060307, "price": 2165.95, "entry_price": 2043.3,
                                           "stop_price": None, "position_pnl_pct": -1.2, "buy_and_hold_pct": 5.7}),
    "sensor.kraken_bot_soleur": ("0.0", {"coin": 0.0, "price": 85.46, "entry_price": None, "stop_price": None,
                                         "position_pnl_pct": None, "buy_and_hold_pct": 2.58}),
    "sensor.kraken_bot_decision": ("2026-11-04 19:15", {
        "summary": "The market bounced.",
        "decisions": [{"date": "2026-11-04 19:15", "pair": "XBTEUR", "price": "71737.4", "model_action": "hold",
                       "confidence": "0.55", "p_up_3d": "0.52", "executed": "none", "rule": "model: hold; stop 67900",
                       "reasoning": "Resistance at \"72k\"."}],
        "history": [{"date": "2026-11-04 19:30", "pair": "XBTEUR", "model_action": "stop", "confidence": "0.0",
                     "p_up_3d": "", "executed": "sell", "rule": "STOP (level 67900)"},
                    {"date": "2026-11-03 08:05", "pair": "ETHEUR", "model_action": "sell", "confidence": "0.62",
                     "executed": "none", "rule": "confidence 0.62 < threshold 0.65"}]}),  # old row without p_up_3d
    "sensor.kraken_bot_performance": ("unknown", {
        "bot": {"return_pct": 3.62, "avg_exposure_pct": 100.0, "cycles": 4},
        "shadow": {"since": "2026-11-03", "trend_rule": "close > SMA50", "buy_and_hold_pct": 3.39, "trend_pct": 0.0},
        "by_action": {"hold": {"signals": 11, "avg_1d_pct": 4.21, "avg_3d_pct": None, "avg_7d_pct": None,
                               "settled_3d": 0, "up_3d_pct": None}},
        "p_up_3d": {"settled": 0, "brier": None, "hit_rate_pct": None, "buckets": []}}),
}

EXPECTED = {  # file -> (positions empty row, decision line, no decision, no data)
    "ha_dashboard.yaml": ("none", "chance of a rise in 3 days 52%", "No decision yet", "No data yet"),
    "ha_dashboard.pl.yaml": ("brak", "szansa wzrostu w 3 dni 52%", "Brak decyzji", "Brak danych"),
}


def render_cards(path, states):
    """Renders the markdown cards like HA: Jinja sandbox + HA's float/round filters."""
    env = jinja_sandbox.ImmutableSandboxedEnvironment()

    def ha_float(value, default=None):
        try:
            return float(value)
        except (TypeError, ValueError):
            if default is None:
                raise
            return default

    def ha_round(value, precision=0):
        value = round(float(value), precision)
        return int(value) if precision == 0 else value

    env.filters.update(float=ha_float, round=ha_round)
    env.globals.update(states=lambda e: states.get(e, ("unknown", {}))[0],
                       state_attr=lambda e, a: states.get(e, (None, {}))[1].get(a))
    dash = yaml.safe_load((ROOT / "dashboards" / path).read_text(encoding="utf-8"))
    cards = [c for s in dash["views"][0]["sections"] for c in s["cards"] if c["type"] == "markdown"]
    assert len(cards) == 4
    return [env.from_string(c["content"]).render() for c in cards]


@pytest.mark.parametrize("path", EXPECTED)
def test_dashboard_renders_with_data(path):
    empty_row, decision_line, _, _ = EXPECTED[path]
    positions, decision, stats, log = render_cards(path, FULL)
    assert "▲ 9.33%" in positions and "67 900" in positions and empty_row in positions
    assert decision_line in decision and "None" not in decision
    assert "+3.62%" in stats and "None" not in stats
    assert "🛑 stop" in log and "**sell**" in log


@pytest.mark.parametrize("path", EXPECTED)
def test_dashboard_renders_before_first_cycle(path):
    """A fresh Home Assistant: no entities yet - the cards must render, not crash."""
    _, _, no_decision, no_data = EXPECTED[path]
    positions, decision, stats, log = render_cards(path, {})
    assert no_decision in decision
    assert no_data in stats


@pytest.mark.parametrize("path", EXPECTED)
def test_dashboard_uses_only_known_entities(path):
    text = (ROOT / "dashboards" / path).read_text(encoding="utf-8")
    used = set(re.findall(r"sensor\.kraken_bot_[a-z_]+", text)) - {"sensor.kraken_bot_"}
    pairs = ("xbteur", "etheur", "soleur")
    known = {f"sensor.kraken_bot_{n}" for n in ("equity", "return", "free_eur", "decision", "performance")}
    known |= {f"sensor.kraken_bot_{p}" for p in pairs} | {f"sensor.kraken_bot_{p}_forecast" for p in pairs}
    assert used <= known


# ---------------------------------------------------------------- price and forecast charts (ApexCharts)
START, END, NOW = 1_790_000_000_000, 1_793_000_000_000, 1_791_000_000_000
CHART_STATES = {
    "sensor.kraken_bot_xbteur": {"price": 71000.0, "entry_price": 65617.0, "stop_price": 67900.0},
    "sensor.kraken_bot_xbteur_forecast": {
        "history": [[NOW - 2 * 86400000, 70000.0], [NOW - 86400000, 70500.0]],
        "forecast": [[NOW, 70800.0, 70800.0, 70800.0], [NOW + 86400000, 71000.0, 69500.0, 72500.0],
                     [NOW + 2 * 86400000, 71100.0, 69000.0, 73300.0], [NOW + 3 * 86400000, 71300.0, 68600.0, 74000.0]],
        "made": "2026-11-05 08:05"},
}
RUN_GENERATORS = """
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = cases.map(c => {
  const hass = {states: Object.fromEntries(Object.entries(c.states).map(([k, a]) => [k, {attributes: a}]))};
  const entity = {attributes: c.states[c.entity] || {}};
  const fn = new Function('entity', 'start', 'end', 'hass', 'moment', c.code);
  return fn(entity, new Date(c.start), new Date(c.end), hass, null);
});
console.log(JSON.stringify(out));
"""


def apex_series(path):
    dash = yaml.safe_load((ROOT / "dashboards" / path).read_text(encoding="utf-8"))
    cards = [c for s in dash["views"][0]["sections"] for c in s["cards"] if c["type"] == "custom:apexcharts-card"]
    assert len(cards) == 3
    return [s for c in cards for s in c["series"]]


def run_generators(series, states):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    cases = [{"entity": s["entity"], "code": s["data_generator"], "states": states, "start": START, "end": END}
             for s in series]
    done = subprocess.run([node, "-e", RUN_GENERATORS], input=json.dumps(cases), capture_output=True,
                          text=True, check=True)
    return json.loads(done.stdout)


@pytest.mark.parametrize("path", EXPECTED)
def test_charts_draw_price_forecast_and_levels(path):
    series = [s for s in apex_series(path) if "xbteur" in s["entity"]]
    price, mid, low, high, entry, stop = run_generators(series, CHART_STATES)
    assert price[:2] == CHART_STATES["sensor.kraken_bot_xbteur_forecast"]["history"] and price[-1][1] == 71000.0
    assert [p[1] for p in mid] == [70800.0, 71000.0, 71100.0, 71300.0]
    assert all(lo[1] <= m[1] <= hi[1] for lo, m, hi in zip(low, mid, high))
    assert entry == [[START, 65617.0], [END, 65617.0]] and stop == [[START, 67900.0], [END, 67900.0]]


@pytest.mark.parametrize("path", EXPECTED)
def test_charts_are_empty_before_first_cycle(path):
    """No entities yet (or a pair without a position): empty series, no JavaScript error."""
    results = run_generators(apex_series(path), {})
    assert all(r == [] for r in results)


@pytest.mark.parametrize("path", EXPECTED)
def test_charts_are_in_eur_and_do_not_extend_into_the_future(path):
    """The forecast entity's own unit is %, so every series sets EUR; ApexCharts would otherwise also draw
    the last price as a flat line to the right edge."""
    for s in apex_series(path):
        assert s["unit"] == "EUR"
        if "_forecast" in s["entity"]:
            assert s["extend_to"] is False
