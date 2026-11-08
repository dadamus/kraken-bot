"""Claude: the prompt, the response schema and the API call."""
import json
import os
import random
from pathlib import Path

import anthropic

from krakenbot import costs

from . import texts

ACTIONS = ("long", "short", "flat", "hold")
SYSTEM_PROMPT = (Path(__file__).resolve().parent / "system_prompt.txt").read_text(encoding="utf-8").rstrip()
last_usage = None  # token counts of the latest call, read by the cycle for the cost tracking

# The trading style ("style" in config.json): how readily the model trades. The rules in code are set
# separately in "risk" (the aggressive preset in DOCS.md lowers min_confidence and raises the size).
STYLES = {
    "cautious": 'Be skeptical. Most days are noise; "flat" and "hold" are normal answers. Prefer no trade over a '
                "weak one.",
    "aggressive": "Trade actively: this run tests an aggressive style on purpose. Look for a tradeable setup in every "
                  "pair every day, in either direction - momentum and breakouts, failed breakouts, mean reversion "
                  "from stretched levels, funding or sentiment extremes. A position with a modest edge is preferred "
                  'over sitting out; use "flat" only when you see no edge at all or the costs would eat it. Being '
                  "wrong often is fine when the winners are larger than the losers: place tight, logical stops and "
                  "let targets run (take_profit 2-4x the stop distance). Still give honest confidence and p_up_3d "
                  "numbers - they are scored; the aggressiveness is in how often you trade, not in overstating "
                  "certainty.",
}


def style(cfg):
    return cfg.get("style") if cfg.get("style") in STYLES else "cautious"


def system_prompt(cfg=None):
    return (SYSTEM_PROMPT.replace("{STYLE}", STYLES[style(cfg or {})])
            .replace("{LANGUAGE}", texts.language_name()))


def number(x, lo=None, hi=None):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def parse_signal(d):
    action = d.get("action")
    stop, tp = number(d.get("stop_price")), number(d.get("take_profit"))
    p_up = number(d.get("p_up_3d"), 0.0, 1.0)
    return {"action": action if action in ACTIONS else "hold",
            "confidence": number(d.get("confidence"), 0.0, 1.0) or 0.0,
            "p_up_3d": round(p_up, 3) if p_up is not None else None,
            "stop_price": stop if stop and stop > 0 else None,
            "take_profit": tp if tp and tp > 0 else None,
            "reasoning": str(d.get("reasoning", ""))[:600]}


def missing_signal():
    return {"action": "hold", "confidence": 0.0, "p_up_3d": None, "stop_price": None, "take_profit": None,
            "reasoning": "no assessment for this pair"}


def fake_signal():
    """--offline: a random call, for testing the plumbing without the API."""
    return {"action": random.choice(ACTIONS), "confidence": round(random.uniform(0.4, 0.9), 2),
            "p_up_3d": round(random.uniform(0.35, 0.65), 2), "stop_price": None, "take_profit": None,
            "reasoning": "OFFLINE TEST - random call"}


def schema(pairs):
    num, nullable = {"type": "number"}, {"anyOf": [{"type": "number"}, {"type": "null"}]}
    decision = {"type": "object", "additionalProperties": False,
                "required": ["pair", "action", "confidence", "p_up_3d", "stop_price", "take_profit", "reasoning"],
                "properties": {"pair": {"type": "string", "enum": list(pairs)},
                               "action": {"type": "string", "enum": list(ACTIONS)},
                               "confidence": num, "p_up_3d": num, "stop_price": nullable, "take_profit": nullable,
                               "reasoning": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False, "required": ["decisions", "summary"],
            "properties": {"decisions": {"type": "array", "items": decision}, "summary": {"type": "string"}}}


def parse_answer(cfg, d):
    by_pair = {str(x.get("pair", "")).upper(): x for x in d.get("decisions", [])}
    signals = {pair: parse_signal(by_pair[pair]) if pair in by_pair else missing_signal() for pair in cfg["pairs"]}
    return signals, str(d.get("summary", ""))[:600]


def ask(cfg, payload):
    """One request with everything; returns ({pair: signal}, summary)."""
    global last_usage
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is missing (add-on option anthropic_api_key)")
    client = anthropic.Anthropic(api_key=key, timeout=300, max_retries=3)
    # Structured outputs: the API guarantees JSON matching the schema; fallbacks="default" re-runs a request a
    # safety classifier declined on the recommended fallback model (same as the Kraken bot).
    resp = client.beta.messages.create(
        model=cfg["model"], max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": cfg.get("effort", "high"),
                       "format": {"type": "json_schema", "schema": schema(cfg["pairs"])}},
        system=system_prompt(cfg),
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)}])
    last_usage = costs.usage_of(resp)  # billed even when the answer is unusable
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"the model's answer is incomplete (stop_reason {resp.stop_reason})")
    return parse_answer(cfg, json.loads(next(b.text for b in resp.content if b.type == "text")))
