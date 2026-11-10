"""The backtest: reading Kraken's files, the replay, the summary."""
import json

import pytest

from intraday import backtest, settings


def test_read_csv_takes_kraken_ohlcvt_and_rejects_other_intervals(tmp_path):
    good = tmp_path / "XBTEUR_60.csv"
    good.write_text("timestamp,open,high,low,close,volume,trades\n"
                    "1700003600,101,103,100,102,5,10\n1700000000,100,102,99,101,4,9\nbroken line\n")
    cs = backtest.read_csv(good)
    assert [c["t"] for c in cs] == [1700000000, 1700003600] and cs[1]["c"] == 102.0 and cs[0]["v"] == 4.0
    minutes = tmp_path / "XBTEUR_1.csv"
    minutes.write_text("1700000000,1,1,1,1,1,1\n1700000060,1,1,1,1,1,1\n")
    with pytest.raises(ValueError, match="60-minute"):
        backtest.read_csv(minutes)
    with pytest.raises(ValueError, match="PAIR=FILE"):
        backtest.load({"pairs": ["XBTEUR"]}, ["XBTEUR"])


def test_daily_candles_from_hourly():
    hours = [{"t": 86400 * 10 + i * 3600, "o": 100 + i, "h": 101 + i, "l": 99 + i, "c": 100.5 + i, "v": 1.0}
             for i in range(30)]
    d = backtest.daily_from_hourly(hours)
    assert len(d) == 2 and d[0]["o"] == 100 and d[0]["c"] == 123.5 and d[0]["h"] == 124 and d[0]["v"] == 24.0


def test_offline_backtest_replays_the_rules_book(icfg):
    icfg["pairs"] = ["XBTEUR", "PAXGEUR", "EURUSD"]
    result = backtest.run(icfg, offline=True)
    a = result["trades"]["all"]
    assert a["n"] > 0 and a["gross_eur"] == pytest.approx(a["net_eur"] + a["spread_eur"] + a["swap_eur"], abs=0.02)
    assert result["equity_eur"] == pytest.approx(1000 + a["net_eur"], abs=0.05)  # everything closed at the end
    assert set(result["trades"]["by_strategy"]) <= {"breakout", "reversion"}
    assert sum(result["regime_days"]["XBTEUR"].values()) in (120, 121)  # 2880 hours over UTC days
    assert json.loads(settings.BACKTEST_FILE.read_text())["date"] == result["date"]
    assert backtest.load_result()["equity_eur"] == result["equity_eur"]
