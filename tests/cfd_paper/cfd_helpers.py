"""Shared helpers for the CFD paper tests (a separate module: the root conftest owns the name "conftest")."""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "kraken_cfd"))  # appended: kraken_bot/addon.py keeps the name "addon"

NOW = datetime(2026, 11, 2, 12, 0)


def sig(action="hold", confidence=0.8, stop=None, tp=None, p_up=0.5):
    return {"action": action, "confidence": confidence, "p_up_3d": p_up, "stop_price": stop, "take_profit": tp,
            "reasoning": "test"}


def fake_markets(prices):
    """A cycle.load_markets stand-in: an ATR of 3 % of the price, no candles."""
    def load(pairs, offline_mode, crypto=None, cfg=None):
        markets = {p: {"atr14": prices[p] * 0.03, "price_now": prices[p]} for p in pairs}
        candles = {p: [] for p in pairs}
        return markets, dict(prices), candles
    return load
