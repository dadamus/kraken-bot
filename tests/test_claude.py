"""The Claude request: request format, response parsing and defence against bad data."""
import json
from types import SimpleNamespace

import pytest

import bot


def test_parse_signal_clamps_and_cleans():
    s = bot.parse_signal({"action": "BUY!!", "confidence": 1.7, "size_pct": 250, "p_up_3d": 1.3,
                          "stop_price": "abc", "reasoning": "x" * 1000})
    assert s["action"] == "hold"  # unknown action = do nothing
    assert s["confidence"] == 1.0
    assert s["size_pct"] == 100.0
    assert s["p_up_3d"] == 1.0
    assert s["stop_price"] is None
    assert len(s["reasoning"]) == 600


@pytest.mark.parametrize("p_up", [None, "", "abc"])
def test_parse_signal_missing_probability(p_up):
    d = {"action": "sell", "confidence": 0.7, "size_pct": 50}
    if p_up is not None:
        d["p_up_3d"] = p_up
    s = bot.parse_signal(d)
    assert s["p_up_3d"] is None and s["action"] == "sell"


def test_parse_signal_keeps_valid_stop():
    assert bot.parse_signal({"action": "buy", "stop_price": "67900"})["stop_price"] == 67900.0
    assert bot.parse_signal({"action": "buy", "stop_price": None})["stop_price"] is None


def test_decision_schema_is_strict():
    schema = bot.decision_schema(["XBTEUR", "ETHEUR"])

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)

    for obj in objects(schema):  # structured outputs require this for every object
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    item = schema["properties"]["decisions"]["items"]
    assert item["properties"]["pair"]["enum"] == ["XBTEUR", "ETHEUR"]
    assert item["properties"]["action"]["enum"] == ["buy", "sell", "hold"]


class FakeClient:
    """Stands in for anthropic.Anthropic: records the request and returns a prepared response."""
    last_kwargs = None
    response = None

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        FakeClient.last_kwargs = kwargs
        return FakeClient.response


def reply(payload, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, stop_details=None,
                           content=[SimpleNamespace(type="thinking", thinking=""),
                                    SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))])


@pytest.fixture
def fake_claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(bot.anthropic, "Anthropic", FakeClient)
    return FakeClient


def ask(cfg):
    return bot.ask_claude(cfg, markets={"XBTEUR": {}}, news=[], portfolio={}, context={})


def test_ask_claude_request_and_parsing(cfg, fake_claude):
    fake_claude.response = reply({"summary": "market \"nervous\"", "decisions": [
        {"pair": "XBTEUR", "action": "buy", "confidence": 0.8, "p_up_3d": 0.6, "size_pct": 40,
         "stop_price": 67900, "reasoning": "resistance \"72k\" broken"},
        {"pair": "etheur", "action": "hold", "confidence": 0.5, "p_up_3d": 0.5, "size_pct": 0,
         "stop_price": None, "reasoning": "-"}]})
    signals, summary = ask(cfg)

    assert summary == 'market "nervous"'  # quotes inside the text no longer break parsing
    assert signals["XBTEUR"]["action"] == "buy" and signals["XBTEUR"]["stop_price"] == 67900
    assert signals["ETHEUR"]["action"] == "hold"  # pair names are case-insensitive
    assert signals["SOLEUR"] == {"action": "hold", "confidence": 0.0, "p_up_3d": None, "stop_price": None,
                                 "size_pct": 0.0, "reasoning": "no model assessment for this pair"}

    kw = fake_claude.last_kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["effort"] == "high"
    assert kw["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in kw["betas"]
    assert 'Write "reasoning" and "summary" in English.' in kw["system"]
    user = json.loads(kw["messages"][0]["content"])
    assert user["buy_limits"]["stop_loss_pct"] == 8


def test_polish_language_asks_for_polish_reasoning(cfg, fake_claude):
    cfg["language"] = "pl"
    bot.set_language(cfg)
    fake_claude.response = reply({"summary": "", "decisions": []})
    signals, _ = ask(cfg)
    assert 'in Polish.' in fake_claude.last_kwargs["system"]
    assert signals["XBTEUR"]["reasoning"] == "brak oceny modelu dla tej pary"


def test_ask_claude_effort_from_config(cfg, fake_claude):
    cfg["effort"] = "medium"
    fake_claude.response = reply({"summary": "", "decisions": []})
    ask(cfg)
    assert fake_claude.last_kwargs["output_config"]["effort"] == "medium"


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_ask_claude_incomplete_answer_raises(cfg, fake_claude, stop_reason):
    fake_claude.response = reply({"summary": "", "decisions": []}, stop_reason=stop_reason)
    with pytest.raises(RuntimeError, match=stop_reason):
        ask(cfg)


def test_ask_claude_requires_key(cfg, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        ask(cfg)
