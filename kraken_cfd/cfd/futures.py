"""Kraken Derivatives (the futures platform) REST API: public market data and, with keys, the account and orders.

Clients in the EEA (Payward Europe Digital Solutions, CySEC) use the same API as everyone else:
https://futures.kraken.com/derivatives/api/v3. The keys are made in the derivatives account (Settings > API),
separately from the spot keys: "Read Only" is enough for the shadow mode, orders need "Full Access" (never
give a key withdrawal rights). There is no public test environment any more (the demo was shut down in 2025).

Orders are refused unless the run allowed them (ALLOW): "none" by default, "reduce" for orders that can only
shrink a position (a protective stop while live positions are wound down), "all" in live mode only.
"""
import base64
import hashlib
import hmac
import os
import time
import urllib.parse
from datetime import datetime, timezone

import requests

from krakenbot import config as kb_config

BASE = "https://futures.kraken.com"
API = "/derivatives/api/v3"
FUNDING = "/derivatives/api/v4/historicalfundingrates"
ACCOUNT_LOG = "/api/history/v3/account-log"
EEA = "europa"  # platformsPermitted: the EEA entity; a contract without it is not offered to EEA clients

ALLOW = "none"
_last_nonce = 0


class OrderRefused(RuntimeError):
    """An order this run is not allowed to send (see ALLOW)."""


def keys():
    return os.environ.get("KRAKEN_FUTURES_API_KEY") or "", os.environ.get("KRAKEN_FUTURES_API_SECRET") or ""


def has_keys():
    key, secret = keys()
    return bool(key and secret)


def sign(secret, endpoint_path, post_data, nonce):
    """Authent: base64(HMAC-SHA512(base64-decoded secret, SHA-256(postData + nonce + path))). The path is the
    request path without the "/derivatives" prefix (/derivatives/api/v3/accounts -> /api/v3/accounts)."""
    path = endpoint_path[len("/derivatives"):] if endpoint_path.startswith("/derivatives") else endpoint_path
    digest = hashlib.sha256((post_data + nonce + path).encode()).digest()
    return base64.b64encode(hmac.new(base64.b64decode(secret), digest, hashlib.sha512).digest()).decode()


def nonce():
    global _last_nonce
    _last_nonce = max(_last_nonce + 1, int(time.time() * 1000))  # strictly increasing
    return str(_last_nonce)


def check(j, what):
    if not isinstance(j, dict) or j.get("result") != "success":
        err = (j or {}).get("error") or (j or {}).get("errors") or j
        raise RuntimeError(f"Kraken Futures {what}: {err}")
    return j


def public(path, params=None):
    r = requests.get(BASE + path, params=params, headers=kb_config.UA, timeout=20)
    if r.status_code == 404:
        raise RuntimeError(f"Kraken Futures {path}: not found")
    r.raise_for_status()
    return check(r.json(), path)


def private(path, data=None, method="POST"):
    key, secret = keys()
    if not key or not secret:
        raise RuntimeError("no Kraken derivatives API key (kraken_futures_api_key / kraken_futures_api_secret)")
    post = urllib.parse.urlencode(data or {})
    n = nonce()
    headers = {**kb_config.UA, "APIKey": key, "Nonce": n, "Authent": sign(secret, path, post, n)}
    if method == "GET":
        r = requests.get(BASE + path + (f"?{post}" if post else ""), headers=headers, timeout=20)
    else:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        r = requests.post(BASE + path, data=post, headers=headers, timeout=20)
    r.raise_for_status()
    j = r.json()
    if path.startswith("/api/history"):  # the history API answers without "result"
        if isinstance(j, dict) and j.get("error"):
            raise RuntimeError(f"Kraken Futures {path}: {j['error']}")
        return j
    return check(j, path.rsplit("/", 1)[-1])


# --- public ---------------------------------------------------------------------------------------------

def instruments(symbols=None):
    """{symbol: contract spec} (the whole list is ~0.8 MB: callers cache what they need)."""
    out = {i["symbol"]: i for i in public(f"{API}/instruments")["instruments"] if "symbol" in i}
    return {s: out[s] for s in symbols if s in out} if symbols is not None else out


def ticker(symbol):
    return public(f"{API}/tickers/{symbol}")["ticker"]


def funding_history(symbol):
    """[(unix time, relative hourly rate)], oldest first: the share of the notional a long pays per hour
    (negative: the long receives). Kraken keeps about a year."""
    rates = public(FUNDING, {"symbol": symbol}).get("rates") or []
    return [(utc_ts(r["timestamp"]), float(r["relativeFundingRate"])) for r in rates]


def utc_ts(text):
    text = text.rstrip("Z").split(".")[0]
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


# --- account (any key) ----------------------------------------------------------------------------------

def flex_account():
    """The multi-collateral wallet: portfolioValue, availableMargin, initialMargin, ... (USD)."""
    accounts = private(f"{API}/accounts", method="GET")["accounts"]
    flex = accounts.get("flex") or next((a for a in accounts.values()
                                          if a.get("type") == "multiCollateralMarginAccount"), None)
    if not flex:
        raise RuntimeError("Kraken Futures: no multi-collateral (flex) wallet on this account")
    return flex


def open_positions():
    return private(f"{API}/openpositions", method="GET").get("openPositions") or []


def open_orders():
    return private(f"{API}/openorders", method="GET").get("openOrders") or []


def fills(last_fill_time=None):
    data = {"lastFillTime": last_fill_time} if last_fill_time else None
    return private(f"{API}/fills", data, method="GET").get("fills") or []


def account_log(since_ms=None, count=500):
    """Wallet bookings (trades, funding, transfers, ...), newest first."""
    data = {"count": count, **({"since": int(since_ms)} if since_ms else {})}
    return private(ACCOUNT_LOG, data, method="GET").get("logs") or []


# --- orders (refused unless ALLOW permits them) ---------------------------------------------------------

def send_order(symbol, side, size, order_type="mkt", stop_price=None, limit_price=None, reduce_only=False,
               trigger_signal=None, cli_ord_id=None):
    """Returns sendStatus ({status, order_id, orderEvents}). Raises when not placed."""
    if ALLOW == "none" or (ALLOW == "reduce" and not reduce_only):
        raise OrderRefused(f"{order_type} {side} {size} {symbol}: orders are off in this mode")
    data = {"orderType": order_type, "symbol": symbol, "side": side, "size": f"{size:g}"}
    if stop_price is not None:
        data["stopPrice"] = f"{stop_price:g}"
    if limit_price is not None:
        data["limitPrice"] = f"{limit_price:g}"
    if reduce_only:
        data["reduceOnly"] = "true"
    if trigger_signal:
        data["triggerSignal"] = trigger_signal
    if cli_ord_id:
        data["cliOrdId"] = cli_ord_id
    status = private(f"{API}/sendorder", data)["sendStatus"]
    if status.get("status") not in ("placed", "partiallyFilled", "filled"):
        raise RuntimeError(f"Kraken Futures sendorder {order_type} {side} {symbol}: {status.get('status')}")
    return status


def edit_order(order_id, stop_price):
    if ALLOW == "none":
        raise OrderRefused(f"edit {order_id}: orders are off in this mode")
    status = private(f"{API}/editorder", {"orderId": order_id, "stopPrice": f"{stop_price:g}"})["editStatus"]
    if status.get("status") != "edited":
        raise RuntimeError(f"Kraken Futures editorder {order_id}: {status.get('status')}")
    return status


def cancel_order(order_id):
    """Cancelling never adds risk, so it is allowed in every mode with a key. Never raises: returns False."""
    try:
        status = private(f"{API}/cancelorder", {"order_id": order_id})["cancelStatus"]
        return status.get("status") in ("cancelled", "notFound")
    except Exception as e:
        print(f"  [kraken] cancel {order_id} failed: {e}")
        return False


def executed(status):
    """(filled size, average price) from a sendStatus' orderEvents."""
    size = cost = 0.0
    for ev in status.get("orderEvents") or []:
        if ev.get("type") == "EXECUTION":
            amount, price = float(ev.get("amount") or 0), float(ev.get("price") or 0)
            size += amount
            cost += amount * price
    return size, (cost / size if size else None)
