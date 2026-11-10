"""Claude: the daily regime per instrument - the prompt, the response schema and the API call."""
import json
import os
import random
from pathlib import Path

import anthropic

from krakenbot import costs

from . import regime, texts

SYSTEM_PROMPT = (Path(__file__).resolve().parent / "system_prompt.txt").read_text(encoding="utf-8").rstrip()
last_usage = None  # token counts of the latest call, read by the cycle for the cost tracking


def system_prompt():
    return SYSTEM_PROMPT.replace("{LANGUAGE}", texts.language_name())


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


def parse_regime(d):
    name = d.get("regime")
    return {"regime": name if name in regime.REGIMES else "no_trade",
            "confidence": round(number(d.get("confidence"), 0.0, 1.0) or 0.0, 3),
            "reasoning": str(d.get("reasoning", ""))[:600]}


def missing():
    return {"regime": "no_trade", "confidence": 0.0, "reasoning": "no assessment for this pair"}


def fake_regime():
    """--offline: a random regime, for testing the plumbing without the API."""
    return {"regime": random.choice(regime.REGIMES), "confidence": round(random.uniform(0.4, 0.9), 2),
            "reasoning": "OFFLINE TEST - random regime"}


def schema(pairs):
    item = {"type": "object", "additionalProperties": False, "required": ["pair", "regime", "confidence", "reasoning"],
            "properties": {"pair": {"type": "string", "enum": list(pairs)},
                           "regime": {"type": "string", "enum": list(regime.REGIMES)},
                           "confidence": {"type": "number"}, "reasoning": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False, "required": ["regimes", "summary"],
            "properties": {"regimes": {"type": "array", "items": item}, "summary": {"type": "string"}}}


def parse_answer(cfg, d):
    by_pair = {str(x.get("pair", "")).upper(): x for x in d.get("regimes", [])}
    regimes = {pair: parse_regime(by_pair[pair]) if pair in by_pair else missing() for pair in cfg["pairs"]}
    return regimes, str(d.get("summary", ""))[:600]


def ask(cfg, payload):
    """One request with everything; returns ({pair: {regime, confidence, reasoning}}, summary)."""
    global last_usage
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is missing (add-on option anthropic_api_key)")
    client = anthropic.Anthropic(api_key=key, timeout=300, max_retries=3)
    # Structured outputs: the API guarantees JSON matching the schema; fallbacks="default" re-runs a request a
    # safety classifier declined on the recommended fallback model (same as the other add-ons).
    resp = client.beta.messages.create(
        model=cfg["model"], max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": cfg.get("effort", "high"),
                       "format": {"type": "json_schema", "schema": schema(cfg["pairs"])}},
        system=system_prompt(),
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)}])
    last_usage = costs.usage_of(resp)  # billed even when the answer is unusable
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"the model's answer is incomplete (stop_reason {resp.stop_reason})")
    return parse_answer(cfg, json.loads(next(b.text for b in resp.content if b.type == "text")))
