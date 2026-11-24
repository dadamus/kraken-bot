"""A fake Kraken Derivatives for the shadow and live tests: futures.private and the public calls replaced by a
tiny exchange with one price per contract, resting stop / take-profit orders and a USD wallet."""
from datetime import datetime, timezone

from .cfd_helpers import NOW

from cfd import futures


def spec(symbol, tick, precision, eea=True, im=0.1):
    return {"symbol": symbol, "tickSize": tick, "contractValueTradePrecision": precision, "maxPositionSize": 1000.0,
            "tradeable": True, "suspended": False, "quote": "USD",
            "platformsPermitted": ["europa", "mtf"] if eea else ["payward_digital_solutions"],
            "marginSchedules": {"europa": {"retail": [{"initialMargin": im}]}} if eea else {},
            "retailMarginLevels": [{"initialMargin": 0.02}]}


SPECS = {"PF_XBTUSD": spec("PF_XBTUSD", 1, 4), "PF_PAXGUSD": spec("PF_PAXGUSD", 0.1, 3),
         "PF_XAUUSD": spec("PF_XAUUSD", 0.01, 3), "PF_EURUSD": spec("PF_EURUSD", 0.00001, 0, eea=False),
         "PF_WTIOILUSD": spec("PF_WTIOILUSD", 0.001, 1)}
FUNDING_FROM = int(NOW.timestamp()) - 30 * 86400


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class FakeFutures:
    def __init__(self):
        self.price = {"PF_XBTUSD": 60000.0, "PF_PAXGUSD": 3600.0, "PF_XAUUSD": 3610.0, "PF_EURUSD": 1.09,
                      "PF_WTIOILUSD": 80.0}
        self.half = {"PF_XBTUSD": 2.0, "PF_PAXGUSD": 0.4, "PF_XAUUSD": 0.6, "PF_EURUSD": 0.0002, "PF_WTIOILUSD": 0.02}
        self.funding = {"PF_XBTUSD": 0.00001, "PF_PAXGUSD": -0.000005, "PF_XAUUSD": 0.0}
        self.wallet = 1090.0  # USD: 1000 EUR at EURUSD 1.09
        self.positions, self.orders, self.fills, self.log, self.calls = {}, {}, [], [], []
        self.fail = set()  # order types (or "edit") to reject
        self.n = 0
        self.ts = NOW.timestamp()

    def install(self, monkeypatch, keys=True):
        monkeypatch.setattr(futures, "private", self.private)
        monkeypatch.setattr(futures, "instruments", lambda symbols=None: {
            s: SPECS[s] for s in (SPECS if symbols is None else symbols) if s in SPECS})
        monkeypatch.setattr(futures, "ticker", self.ticker)
        monkeypatch.setattr(futures, "funding_history", lambda sym: [
            (t, self.funding.get(sym, 0.0)) for t in range(FUNDING_FROM, FUNDING_FROM + 90 * 86400, 3600)])
        if keys:
            monkeypatch.setenv("KRAKEN_FUTURES_API_KEY", "key")
            monkeypatch.setenv("KRAKEN_FUTURES_API_SECRET", "c2VjcmV0")
        return self

    # --- market ---
    def ticker(self, sym):
        p, h = self.price[sym], self.half[sym]
        return {"symbol": sym, "bid": p - h, "ask": p + h, "markPrice": p,
                "relativeFundingRate": self.funding.get(sym, 0.0)}

    def value(self):
        unreal = sum((self.price[s] - p["price"]) * p["size"] * (1 if p["side"] == "long" else -1)
                     for s, p in self.positions.items())
        return self.wallet + unreal

    def execute(self, sym, side, size, price, oid, fill_type="taker"):
        sign = 1 if side == "buy" else -1
        pos = self.positions.get(sym)
        held = (pos["size"] if pos["side"] == "long" else -pos["size"]) if pos else 0.0
        new = round(held + sign * size, 10)
        if pos and held * sign < 0:  # reducing: realise the P&L of the reduced part
            closed = min(size, abs(held))
            self.wallet += (price - pos["price"]) * closed * (1 if held > 0 else -1)
        if new == 0:
            self.positions.pop(sym, None)
        elif not pos or held * new < 0:
            self.positions[sym] = {"side": "long" if new > 0 else "short", "size": abs(new), "price": price}
        elif abs(new) > abs(held):
            avg = (pos["price"] * abs(held) + price * size) / abs(new)
            self.positions[sym] = {"side": pos["side"], "size": abs(new), "price": avg}
        else:
            pos["size"] = abs(new)
        self.wallet -= price * size * 0.0005  # taker fee
        self.fills.append({"fill_id": f"F{len(self.fills) + 1}", "symbol": sym.lower(), "side": side, "order_id": oid,
                           "size": size, "price": price, "fillTime": iso(self.ts), "fillType": fill_type})

    def move(self, sym, price, minutes=10):
        """The price moves; resting orders fire at their level (the mark price is the price here)."""
        self.ts += minutes * 60
        self.price[sym] = price
        for oid, o in list(self.orders.items()):
            if o["symbol"] != sym or sym not in self.positions:
                continue
            sell = o["side"] == "sell"
            stop = o["stopPrice"]
            fire = ((price <= stop) if sell else (price >= stop)) if o["orderType"] == "stp" \
                else ((price >= stop) if sell else (price <= stop))
            if fire:
                del self.orders[oid]
                self.execute(sym, o["side"], min(o["size"], self.positions[sym]["size"]), stop, oid)

    # --- private API ---
    def private(self, path, data=None, method="POST"):
        data = dict(data or {})
        name = path.rsplit("/", 1)[-1]
        self.calls.append((name, data))
        if name == "accounts":
            return {"accounts": {"flex": {"type": "multiCollateralMarginAccount", "portfolioValue": self.value(),
                                          "availableMargin": self.value() * 0.9}}}
        if name == "openpositions":
            return {"openPositions": [{"symbol": s.lower(), "side": p["side"], "size": p["size"], "price": p["price"]}
                                      for s, p in self.positions.items()]}
        if name == "openorders":
            return {"openOrders": [dict(o) for o in self.orders.values()]}
        if name == "fills":
            return {"fills": list(self.fills)}
        if name == "account-log":
            return {"logs": list(self.log)}
        if name == "sendorder":
            return {"sendStatus": self.send(data)}
        if name == "editorder":
            if "edit" in self.fail:
                raise RuntimeError("Kraken Futures editorder: rejected")
            self.orders[data["orderId"]]["stopPrice"] = float(data["stopPrice"])
            return {"editStatus": {"status": "edited", "orderId": data["orderId"]}}
        if name == "cancelorder":
            gone = self.orders.pop(data["order_id"], None)
            return {"cancelStatus": {"status": "cancelled" if gone else "notFound"}}
        raise AssertionError(path)

    def send(self, data):
        otype, sym, side, size = data["orderType"], data["symbol"], data["side"], float(data["size"])
        if otype in self.fail:
            raise RuntimeError(f"Kraken Futures sendorder: {otype} rejected")
        self.n += 1
        oid = f"O{self.n}"
        if otype == "mkt":
            p, h = self.price[sym], self.half[sym]
            price = p + h if side == "buy" else p - h
            self.execute(sym, side, size, price, oid)
            return {"status": "placed", "order_id": oid,
                    "orderEvents": [{"type": "EXECUTION", "amount": size, "price": price}]}
        self.orders[oid] = {"order_id": oid, "symbol": sym, "side": side, "orderType": otype, "size": size,
                            "stopPrice": float(data["stopPrice"]), "reduceOnly": data.get("reduceOnly") == "true",
                            "triggerSignal": data.get("triggerSignal")}
        return {"status": "placed", "order_id": oid, "orderEvents": []}
