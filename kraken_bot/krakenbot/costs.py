"""What the model calls cost: tokens and an estimate in USD per call, this month and in total.

Prices are Anthropic's first-party list prices in USD per million tokens (input, output); cache reads cost
0.1x input and cache writes 1.25x. With server-side fallback the answer may come from another model, so
the price follows the model that answered. Override or extend with "api_prices_usd_per_mtok" in config.json.
"""
from datetime import datetime

PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0),
          "claude-opus-4-7": (5.0, 25.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-sonnet-5": (2.0, 10.0),
          "claude-fable-5-1": (10.0, 50.0), "claude-fable-5": (10.0, 50.0), "claude-haiku-5-5": (0.1, 0.5)}
UNKNOWN = (10.0, 50.0)  # an unknown model is priced high rather than shown as cheap


def usage_of(resp):
    """Token counts from an API response (None when it carries no usage)."""
    u = getattr(resp, "usage", None)
    if u is None:
        return None
    return {"model": getattr(resp, "model", None) or "",
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0}


def cost_usd(cfg, usage):
    prices = {**PRICES, **{k: tuple(v) for k, v in (cfg.get("api_prices_usd_per_mtok") or {}).items()}}
    p_in, p_out = prices.get(usage["model"]) or prices.get(cfg.get("model")) or UNKNOWN
    return (usage["input_tokens"] * p_in + usage["output_tokens"] * p_out
            + usage["cache_read_tokens"] * p_in * 0.1 + usage["cache_write_tokens"] * p_in * 1.25) / 1e6


def record(cfg, state, usage, cycle, now=None):
    """Adds one call to state["api_usage"]: this month's and total calls, tokens and cost."""
    now = now or datetime.now()
    month = now.strftime("%Y-%m")
    cost = cost_usd(cfg, usage)
    u = state.get("api_usage") or {}
    if u.get("month") != month:
        u = {**u, "month": month, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    tokens_in = usage["input_tokens"] + usage["cache_read_tokens"] + usage["cache_write_tokens"]
    u["calls"] += 1
    u["input_tokens"] += tokens_in
    u["output_tokens"] += usage["output_tokens"]
    u["cost_usd"] = round(u["cost_usd"] + cost, 4)
    u["total_calls"] = u.get("total_calls", 0) + 1
    u["total_cost_usd"] = round(u.get("total_cost_usd", 0.0) + cost, 4)
    u["last"] = {"date": now.strftime("%Y-%m-%d %H:%M"), "cycle": cycle, "model": usage["model"],
                 "input_tokens": tokens_in, "output_tokens": usage["output_tokens"], "cost_usd": round(cost, 4)}
    state["api_usage"] = u
    return cost
