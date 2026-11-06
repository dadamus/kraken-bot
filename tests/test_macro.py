"""Context from outside crypto: US markets (FRED), the macro calendar and the Coinbase premium."""
from datetime import datetime, timezone

import pytest
import requests

from krakenbot import macro, market


class Resp:
    def __init__(self, text="", data=None):
        self.text, self._data = text, data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_series_summary_percent_and_points():
    rows = [(f"2026-10-{d:02d}", 100.0 + d) for d in range(1, 23)]
    pct = macro.series_summary(rows)
    assert pct["last"] == 122.0 and pct["date"] == "2026-10-22"
    assert pct["change_1d_pct"] == pytest.approx(0.83, abs=0.01)
    assert pct["change_20d_pct"] == pytest.approx(19.61, abs=0.01)
    pts = macro.series_summary([("a", 4.5), ("b", 4.9)], level=True)
    assert pts["change_1d_points"] == 0.4 and pts["change_5d_points"] is None
    assert macro.series_summary([]) is None


def test_fred_csv_skips_holidays(monkeypatch):
    csv_text = "observation_date,DGS10\n2026-11-02,4.91\n2026-11-03,.\n2026-11-04,4.90\n2026-11-05,\n"
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp(csv_text))
    assert macro.fetch_fred("DGS10") == [("2026-11-02", 4.91), ("2026-11-04", 4.9)]


def test_us_markets_keeps_what_works(monkeypatch):
    def fake(series_id, days=60):
        if series_id == "VIXCLS":
            raise RuntimeError("down")
        return [("2026-11-04", 100.0), ("2026-11-05", 95.0)]

    monkeypatch.setattr(macro, "fetch_fred", fake)
    out = macro.fetch_us_markets()
    assert "vix" not in out and out["nasdaq100"]["change_1d_pct"] == -5.0
    assert out["us10y_yield_pct"]["change_1d_points"] == -5.0


def test_us_markets_none_when_everything_fails(monkeypatch):
    monkeypatch.setattr(macro, "fetch_fred", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert macro.fetch_us_markets() is None


def test_calendar_keeps_high_impact_us_events_from_the_last_day():
    now = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)
    events = [
        {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-11-06T08:30:00-05:00",
         "impact": "High", "forecast": "65K", "previous": "29K"},
        {"title": "FOMC", "country": "USD", "date": "2026-11-04T14:00:00-05:00", "impact": "High"},  # too old
        {"title": "ECB", "country": "EUR", "date": "2026-11-07T08:00:00-05:00", "impact": "High"},
        {"title": "Claims", "country": "USD", "date": "2026-11-07T08:30:00-05:00", "impact": "Medium"},
        {"title": "CPI m/m", "country": "USD", "date": "2026-11-07T08:30:00-05:00", "impact": "High",
         "forecast": "", "previous": "0.3%"},
        {"title": "broken", "country": "USD", "impact": "High"},
    ]
    out = macro.calendar_events(events, now)
    assert [e["event"] for e in out] == ["Non-Farm Employment Change", "CPI m/m"]
    assert out[0] == {"time_utc": "2026-11-06 13:30", "event": "Non-Farm Employment Change", "in_hours": -4.5,
                      "forecast": "65K", "previous": "29K"}
    assert out[1]["in_hours"] == 19.5 and out[1]["forecast"] is None


def test_coinbase_premium_matches_hours():
    coinbase = {3600 * h: 100.0 + h * 0.01 for h in range(30)}
    kraken = {3600 * h: 100.0 for h in range(1, 29)}  # no candle for hour 0 and the open hour 29
    out = macro.premium_summary(coinbase, kraken, 100.05, 100.0)
    assert out["premium_now_pct"] == 0.05
    assert len(out["premium_last_6h_pct"]) == 6 and out["premium_last_6h_pct"][-1] == 0.28
    assert out["premium_avg_24h_pct"] == pytest.approx(0.165, abs=0.001)  # hours 5..28
    assert macro.premium_summary({}, {}, 1.0, 1.0)["premium_avg_24h_pct"] is None


def test_coinbase_premium_fetch(monkeypatch):
    def fake_get(url, **kwargs):
        if url.endswith("/candles"):
            return Resp(data=[[7200, 1, 2, 1, 101.0, 5], [3600, 1, 2, 1, 100.5, 5]])  # newest first
        return Resp(data={"price": "102.0"})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(market, "fetch_ohlc", lambda pair, days, interval: (
        [{"t": 3600, "c": 100.0}, {"t": 7200, "c": 100.0}], 100.0))
    out = macro.fetch_coinbase_premium()
    assert out["premium_now_pct"] == 2.0 and out["premium_last_6h_pct"] == [0.5, 1.0]


def test_calendar_is_cached_in_the_state(monkeypatch):
    calls = []
    event = {"title": "CPI m/m", "country": "USD", "date": "2099-01-01T08:30:00-05:00", "impact": "High"}

    def fake_get(url, **kwargs):
        calls.append(url)
        return Resp(data=[event, {**event, "country": "EUR"}])

    monkeypatch.setattr(requests, "get", fake_get)
    state = {}
    assert [e["event"] for e in macro.fetch_macro_calendar(state)] == ["CPI m/m"]
    assert len(state["macro_calendar_cache"]["events"]) == 1
    macro.fetch_macro_calendar(state)
    assert len(calls) == 1  # fresh: not downloaded again

    state["macro_calendar_cache"]["fetched"] = "2000-01-01 00:00"  # stale, and the feed is down
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("429")))
    assert [e["event"] for e in macro.fetch_macro_calendar(state)] == ["CPI m/m"]  # the week's copy
    state["macro_calendar_cache"]["week"] = "2000-W01"
    with pytest.raises(RuntimeError):
        macro.fetch_macro_calendar(state)
