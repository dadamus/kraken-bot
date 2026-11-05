"""Private Kraken API: live balance, entry prices, deposits/withdrawals and market orders."""
import base64
import hashlib
import hmac
import json
import math
import os
import time
import urllib.parse
from datetime import datetime

import requests

from . import config, portfolio
from .i18n import t


def kraken_private(path, data):
    key, secret = os.environ.get("KRAKEN_API_KEY"), os.environ.get("KRAKEN_API_SECRET")
    if not key or not secret:
        raise RuntimeError(t("no_kraken_keys"))
    data = dict(data, nonce=str(int(time.time() * 1000)))
    post = urllib.parse.urlencode(data)
    sha = hashlib.sha256((data["nonce"] + post).encode()).digest()
    sig = hmac.new(base64.b64decode(secret), path.encode() + sha, hashlib.sha512)
    r = requests.post(config.KRAKEN + path, data=data, timeout=20,
                      headers={**config.UA, "API-Key": key, "API-Sign": base64.b64encode(sig.digest()).decode()})
    r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError(f"Kraken: {j['error']}")
    return j["result"]


def kraken_pairs(pairs):
    """Pair data from Kraken: internal name, asset codes (XBTEUR -> XXBT / ZEUR), precision, minimum."""
    r = requests.get(f"{config.KRAKEN}/0/public/AssetPairs", params={"pair": ",".join(pairs)},
                     headers=config.UA, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"Kraken: {data['error']}")
    info = {v["altname"]: {"key": k, "base": v["base"], "quote": v["quote"],
                           "lot_decimals": int(v["lot_decimals"]), "ordermin": float(v["ordermin"])}
            for k, v in data["result"].items() if v["altname"] in pairs}
    if set(pairs) - set(info):
        raise RuntimeError(t("unknown_pairs", pairs=sorted(set(pairs) - set(info))))
    if len({i["quote"] for i in info.values()}) != 1:
        raise RuntimeError(t("mixed_quotes"))
    return info


def kraken_trades(max_pages=10):
    trades, ofs = [], 0
    for _ in range(max_pages):  # Kraken returns 50 trades per page
        res = kraken_private("/0/private/TradesHistory", {"ofs": ofs})
        page = list(res["trades"].values())
        trades += page
        ofs += len(page)
        if not page or ofs >= int(res["count"]):
            break
    return sorted(trades, key=lambda x: float(x["time"]))


def kraken_ledger(since, max_pages=10):
    """Deposits and withdrawals booked after `since` (unix time), oldest first."""
    entries = []
    for kind in ("deposit", "withdrawal"):
        ofs = 0
        for _ in range(max_pages):  # Kraken returns 50 entries per page
            res = kraken_private("/0/private/Ledgers", {"type": kind, "start": since, "ofs": ofs})
            page = list(res["ledger"].values())
            entries += page
            ofs += len(page)
            if not page or ofs >= int(res["count"]):
                break
    return sorted(entries, key=lambda x: float(x["time"]))


def apply_cash_flows(state, info, prices, entries):
    """Moves the return/drawdown reference (start_eur) by money deposited or withdrawn, so a deposit
    is not counted as profit. Coins are valued at the current price; other assets are ignored."""
    quote = next(iter(info.values()))["quote"]
    value = {quote: 1.0, **{i["base"]: prices[p] for p, i in info.items()}}
    for x in entries:
        state["ledger_checked"] = max(state["ledger_checked"], float(x["time"]))
        if x["type"] not in ("deposit", "withdrawal") or x["asset"] not in value:
            continue
        amount = float(x["amount"]) - float(x.get("fee") or 0)  # how much the balance changed
        eur = amount * value[x["asset"]]
        state["start_eur"] = round(state["start_eur"] + eur, 2)
        state.setdefault("cash_flows", []).append(
            {"time": datetime.fromtimestamp(float(x["time"])).strftime("%Y-%m-%d %H:%M"),
             "type": x["type"], "asset": x["asset"], "amount": amount, "eur": round(eur, 2)})
        print(t("cash_flow_line", kind=t(f"cash_flow_{x['type']}"), amount=amount, asset=x["asset"],
                eur=eur, start=state["start_eur"]))


def manual_entry_prices(cfg):
    """Purchase prices entered by hand (config.json "entry_prices" or the add-on option) - for coins
    bought outside the trade history, e.g. via "Buy crypto" in the app or deposited from elsewhere."""
    prices = {**cfg.get("entry_prices", {}), **json.loads(os.environ.get("BOT_ENTRY_PRICES") or "{}")}
    return {k.upper(): float(v) for k, v in prices.items() if v}


def position_from_trades(trades, info, pair, coin, manual_price, min_coin):
    """Average cost of the current position. Coins outside the trade history (bought in the app,
    deposited) are treated as bought first at the manually entered price. Buys change the average,
    sells do not. Returns (entry price, source) or (None, None); source is "history", "manual"
    or "history+manual"."""
    own = [x for x in trades if x["pair"] in (info["key"], pair)]
    net = sum(float(x["vol"]) * (1 if x["type"] == "buy" else -1) for x in own)
    outside = coin - net
    qty = outside if manual_price and outside >= min_coin else 0.0
    cost = qty * (manual_price or 0)
    for x in own:
        vol = float(x["vol"])
        if x["type"] == "buy":
            qty += vol
            cost += float(x["cost"]) + float(x["fee"])
        elif qty > 0:
            sold = min(vol, qty)
            cost *= 1 - sold / qty
            qty -= sold
    if qty < 1e-12:
        return None, None
    if outside >= min_coin and manual_price:
        return cost / qty, "history+manual" if own else "manual"
    return cost / qty, "history"


def sync_live(cfg, state, prices):
    """Overwrites free EUR and positions with the real Kraken balance. Returns pair data for orders."""
    info = kraken_pairs(cfg["pairs"])
    bal = {k: float(v) for k, v in kraken_private("/0/private/Balance", {}).items()}
    trades = kraken_trades()
    manual = manual_entry_prices(cfg)
    r = cfg["risk"]
    state["eur"] = bal.get(next(iter(info.values()))["quote"], 0.0)
    for pair, i in info.items():
        coin = bal.get(i["base"], 0.0)
        entry = source = None
        min_coin = r["min_trade_eur"] / prices[pair]  # dust left after a sale is ignored
        manual_price = manual.get(pair)
        if manual_price and not prices[pair] / 3 <= manual_price <= prices[pair] * 3:
            # the usual mistake: the total amount paid entered instead of the price per coin
            hint = t("implausible_entry_hint", manual=manual_price, per_coin=manual_price / coin) if coin else ""
            print(t("implausible_entry", pair=pair, manual=manual_price, price=prices[pair], hint=hint))
            manual_price = None
        if coin >= min_coin:
            entry, source = position_from_trades(trades, i, pair, coin, manual_price, min_coin)
        if entry:
            pnl = (prices[pair] / entry - 1) * 100
            print(t("entry_line", pair=pair, entry=entry, source=t(f"entry_source_{source}"), pnl=pnl))
            if pnl <= -r["stop_loss_pct"] or (r.get("take_profit_pct") and pnl >= r["take_profit_pct"]):
                print(t("beyond_threshold", pair=pair))
        elif coin * prices[pair] >= r["min_trade_eur"]:
            print(t("no_entry_price", pair=pair))
        old_stop = state["positions"].get(pair, {}).get("stop_price")  # Kraken knows nothing about our stop
        state["positions"][pair] = {"coin": coin, "entry_price": entry, "entry_source": source,
                                    "stop_price": old_stop if coin >= min_coin else None}
    if not state.get("synced_at"):  # first live start: the reference point for return and drawdown
        state["start_eur"] = round(portfolio.equity_of(state, prices), 2)
    if not state.get("ledger_checked"):  # only flows after this moment move the reference point
        state["ledger_checked"] = time.time()
    else:
        try:
            apply_cash_flows(state, info, prices, kraken_ledger(state["ledger_checked"]))
        except Exception as e:
            print(t("ledger_failed", err=e))
    state["synced_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(t("balance_line", eur=state["eur"],
            coins=", ".join(f"{p} {state['positions'][p]['coin']:.8f}" for p in info)))
    return info


def live_execute(cfg, info, order, price):
    """Market order on Kraken. Returns False when the order did not go through."""
    if order["side"] == "buy":  # reserve for fee and slippage so EUR does not run short
        reserve = (cfg["fee_pct"] + cfg.get("slippage_pct", 0.1)) / 100
        volume = order["eur"] * (1 - reserve) / price
    else:
        volume = order["coin"]
    dec = info["lot_decimals"]
    volume = math.floor(volume * 10 ** dec) / 10 ** dec
    if volume < info["ordermin"]:
        print(t("volume_below_min", volume=volume, min=info["ordermin"]))
        return False
    params = {"pair": info["key"], "type": order["side"], "ordertype": "market",
              "volume": f"{volume:.{dec}f}"}
    if cfg.get("live_validate_only", True):
        params["validate"] = "true"  # Kraken checks the order but does NOT place it
    try:
        res = kraken_private("/0/private/AddOrder", params)
    except Exception as e:
        print(t("order_rejected", err=e))
        return False
    print(f"  Kraken: {res}")
    return True
