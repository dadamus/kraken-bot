"""Claude: the system prompt, the response schema, the context and the API call."""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import anthropic

from . import costs, journal
from .i18n import language_name, t

# The prompt is always English (the model works best with one prompt); only the answer's language changes.
# It lives in a text file so it reads and diffs like prose. {LANGUAGE} is filled in at call time.
last_usage = None  # token counts of the latest call, read by the cycle for the cost tracking

SYSTEM_PROMPT = (Path(__file__).resolve().parent / "system_prompt.txt").read_text(encoding="utf-8").rstrip()


def system_prompt():
    return SYSTEM_PROMPT.replace("{LANGUAGE}", language_name())


def parse_watch(w):
    """What would change the call: price levels that start an extra cycle and the condition in words."""
    w = w if isinstance(w, dict) else {}

    def level(key):
        try:
            v = float(w.get(key))
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    return {"above": level("price_above"), "below": level("price_below"),
            "condition": str(w.get("condition") or "").strip()[:300]}


def parse_signal(d):
    action = d.get("action", "hold")
    if action not in ("buy", "sell", "hold"):
        action = "hold"
    try:
        p_up = round(max(0.0, min(1.0, float(d["p_up_3d"]))), 3)
    except (KeyError, TypeError, ValueError):
        p_up = None
    try:
        stop = float(d["stop_price"]) if d.get("stop_price") else None
    except (TypeError, ValueError):
        stop = None
    return {"action": action, "stop_price": stop,
            "confidence": max(0.0, min(1.0, float(d.get("confidence", 0)))),
            "p_up_3d": p_up,
            "size_pct": max(0.0, min(100.0, float(d.get("size_pct", 0)))),
            "reasoning": str(d.get("reasoning", ""))[:600],
            "watch": parse_watch(d.get("watch"))}


def decision_schema(pairs):
    num, nullable = {"type": "number"}, {"anyOf": [{"type": "number"}, {"type": "null"}]}
    watch = {"type": "object", "additionalProperties": False,
             "required": ["price_above", "price_below", "condition"],
             "properties": {"price_above": nullable, "price_below": nullable, "condition": {"type": "string"}}}
    decision = {"type": "object", "additionalProperties": False,
                "required": ["pair", "action", "confidence", "p_up_3d", "size_pct", "stop_price", "reasoning",
                             "watch"],
                "properties": {"pair": {"type": "string", "enum": list(pairs)},
                               "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
                               "confidence": num, "p_up_3d": num, "size_pct": num,
                               "stop_price": nullable, "reasoning": {"type": "string"}, "watch": watch}}
    return {"type": "object", "additionalProperties": False, "required": ["decisions", "summary"],
            "properties": {"decisions": {"type": "array", "items": decision},
                           "summary": {"type": "string"}}}


def ask_claude(cfg, markets, news, portfolio, context):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(t("no_anthropic_key"))
    r_cfg = cfg["risk"]
    user = json.dumps({"markets": markets, "portfolio": portfolio,
                       "buy_limits": {"max_total_exposure_pct": r_cfg["max_exposure_pct"],
                                      "max_exposure_per_pair_pct": r_cfg.get("max_exposure_per_pair_pct",
                                                                             r_cfg["max_exposure_pct"]),
                                      "max_trade_eur": r_cfg["max_trade_eur"],
                                      "stop_loss_pct": r_cfg["stop_loss_pct"],
                                      "min_confidence": r_cfg["min_confidence"]},
                       "news_last_24h": news, **context},
                      ensure_ascii=False, indent=1)
    # Structured outputs: the API guarantees valid JSON matching the schema. fallbacks="default": if a
    # safety classifier declines the request, the API re-runs it on the recommended fallback model.
    client = anthropic.Anthropic(api_key=key, timeout=300, max_retries=3)
    resp = client.beta.messages.create(
        model=cfg["model"], max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": cfg.get("effort", "high"),
                       "format": {"type": "json_schema", "schema": decision_schema(cfg["pairs"])}},
        system=system_prompt(), messages=[{"role": "user", "content": user}])
    global last_usage
    last_usage = costs.usage_of(resp)  # billed even when the answer is unusable
    if resp.stop_reason != "end_turn":
        raise RuntimeError(t("claude_incomplete", reason=resp.stop_reason,
                             details=getattr(resp, "stop_details", None)))
    d = json.loads(next(b.text for b in resp.content if b.type == "text"))
    by_pair = {str(x.get("pair", "")).upper(): x for x in d.get("decisions", [])}
    signals = {}
    for pair in cfg["pairs"]:
        if pair in by_pair:
            signals[pair] = parse_signal(by_pair[pair])
        else:  # the model skipped the pair -> do nothing
            signals[pair] = {"action": "hold", "confidence": 0.0, "p_up_3d": None, "stop_price": None,
                             "size_pct": 0.0, "reasoning": t("missing_assessment"), "watch": parse_watch(None)}
    return signals, str(d.get("summary", ""))[:600]


def last_exits(pairs, prices, days=30, now=None):
    """Per pair the last sale (by the model or a stop / take-profit) with no purchase since, from the last
    `days` days: buying back above that price means the sale cost money versus simply holding."""
    since = ((now or datetime.now()) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    rows = journal.read_log()
    result = {}
    for pair in pairs:
        last = None
        for r in rows:
            if r["pair"] != pair:
                continue
            if r["executed"] == "buy":
                last = None
            elif r["executed"] == "sell":
                last = r
        if last and last["date"] >= since:
            price = float(last["price"])
            result[pair] = {"date": last["date"], "price": price,
                            "by": "stop" if last["model_action"] == "stop" else "model",
                            "price_vs_exit_pct": round((prices[pair] / price - 1) * 100, 2)}
    return result


def recent_decisions(pairs, prices, days=7):
    """The model's recent calls and what the price did afterwards, so it can see whether it was right.
    Also automatic stop / take-profit sales between cycles (action "stop") from the same period."""
    rows = journal.read_log()
    # the last `days` calendar days with a call (there can be several cycles a day)
    keep_days = set(sorted({r["date"][:10] for r in rows if r["model_action"] != "stop"})[-days:])
    since = min(keep_days) if keep_days else ""
    keep = {r["date"] for r in rows if r["model_action"] != "stop" and r["date"][:10] in keep_days}

    def row(r):
        out = {"date": r["date"], "action": r["model_action"], "confidence": float(r["confidence"] or 0),
               "p_up_3d": float(r["p_up_3d"]) if r.get("p_up_3d") else None,
               "executed": r["executed"], "price_then": float(r["price"]),
               "price_change_since_pct": round((prices[pair] / float(r["price"]) - 1) * 100, 2)}
        if r["model_action"] == "stop":
            out["rule"] = r["rule"]  # e.g. "STOP (level 67900)" or "TAKE-PROFIT"
        elif r.get("watch"):
            out["watch"] = r["watch"]  # the condition the model was waiting for
        return out

    result = {}
    for pair in pairs:
        result[pair] = [row(r) for r in rows if r["pair"] == pair and
                        (r["date"] in keep or (r["model_action"] == "stop" and r["date"] >= since))]
    return result
