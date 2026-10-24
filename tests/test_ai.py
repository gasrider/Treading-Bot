"""KI-Analyst mit simuliertem Claude-Client (keine echten API-Aufrufe)."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import anthropic
import httpx2
import pytest

from tradingbot.ai import FALLBACK_BETA, REVIEW_SCHEMA, AIAnalyst


def usage(inp=1000, out=200, searches=0):
    return NS(input_tokens=inp, output_tokens=out, cache_creation_input_tokens=0, cache_read_input_tokens=0,
              server_tool_use=NS(web_search_requests=searches) if searches else None)


def text_response(text, stop="end_turn", citations=None, searches=0):
    return NS(content=[NS(type="text", text=text, citations=citations or [])], stop_reason=stop,
              usage=usage(searches=searches), model="claude-opus-5-5")


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def analyst(responses, **cfg):
    client = FakeClient(responses)
    return AIAnalyst({"mode": "veto", **cfg}, "", {}, client=client), client


CONTEXT = {"instrument": {"symbol": "AAPL"}, "signal": {"kurs": 200}}


def test_review_approve_uses_structured_output_and_fallbacks():
    ai, client = analyst([text_response(json.dumps(
        {"decision": "approve", "confidence": "hoch", "reason": "Sauberer Trend.", "risks": []}))])
    review = ai.review_entry(CONTEXT)
    assert review.approve and review.decision == "approve" and review.reason == "Sauberer Trend."
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default" and call["betas"] == [FALLBACK_BETA]
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": REVIEW_SCHEMA}
    assert call["output_config"]["effort"] == "medium" and "thinking" not in call
    assert "AAPL" in call["messages"][0]["content"]
    assert review.cost_usd == pytest.approx((1000 * 4 + 200 * 20) / 1e6)


def test_review_reject_is_recorded_as_shadow_and_tracked():
    ai, _ = analyst([text_response(json.dumps(
        {"decision": "reject", "confidence": "mittel", "reason": "Quartalszahlen morgen.", "risks": ["Earnings"]}))])
    review = ai.review_entry(CONTEXT)
    assert not review.approve and review.decision == "reject"
    ai.record("AAPL", review, True, {"price": 100.0, "stop": 95.0, "tp": 110.0})
    ai.update_shadows({"AAPL": 102.0})
    assert ai.state["shadow"][0]["status"] == "offen" and ai.state["shadow"][0]["result_pct"] == 2.0
    ai.update_shadows({"AAPL": 94.0})
    assert ai.state["shadow"][0]["status"] == "wäre Verlust"
    bilanz = ai.summary()["bilanz"]
    assert bilanz == {"rejected": 1, "would_lose": 1, "would_win": 0, "open": 0, "avoided_pct": 5.0}


def test_shadow_expires_after_two_weeks():
    ai, _ = analyst([])
    old = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    ai.state["shadow"].append({"t": old, "symbol": "X", "entry": 100, "stop": 90, "tp": 120, "status": "offen",
                               "result_pct": None, "reason": "", "enforced": True})
    ai.update_shadows({"X": 104})
    assert ai.state["shadow"][0]["status"] == "abgelaufen" and ai.state["shadow"][0]["result_pct"] == 4.0


def api_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("Fehler", response=httpx2.Response(status, request=request), body=None)


@pytest.mark.parametrize("failure, text", [
    (lambda: api_error(anthropic.RateLimitError, 429), "Rate-Limit"),
    (lambda: api_error(anthropic.AuthenticationError, 401), "API-Schlüssel"),
    (lambda: anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")), "Netzwerk"),
    (lambda: text_response("", stop="refusal"), "abgelehnt"),
    (lambda: text_response("kein json"), "nicht lesbar"),
])
def test_review_failures_fall_back_to_rules(failure, text):
    ai, _ = analyst([failure()])
    review = ai.review_entry(CONTEXT)
    assert review.decision == "error" and review.approve and text in review.reason


def test_on_error_reject_blocks():
    ai, _ = analyst([api_error(anthropic.InternalServerError, 500)], on_error="reject")
    review = ai.review_entry(CONTEXT)
    assert review.decision == "error" and not review.approve


def test_monthly_budget_stops_calls():
    ai, client = analyst([], monthly_budget_usd=1.0)
    ai._month()["usd"] = 1.5
    review = ai.review_entry(CONTEXT)
    assert review.decision == "error" and "Monatsbudget" in review.reason and client.calls == []


def test_off_mode_and_missing_key():
    assert not AIAnalyst({"mode": "off"}, "key", {}).available
    assert not AIAnalyst({"mode": "veto"}, "", {}).available
    assert AIAnalyst({"mode": "veto"}, "", {}).review_entry(CONTEXT).approve


def test_market_brief_with_web_search_and_pause_turn():
    paused = NS(content=[NS(type="server_tool_use", name="web_search")], stop_reason="pause_turn",
                usage=usage(searches=2), model="claude-opus-5-5")
    final = text_response("## Lage\nRuhig.", citations=[NS(url="https://news.example/a", title="Nachricht A")],
                          searches=1)
    ai, client = analyst([paused, final], brief_hour_utc=0)
    assert ai.brief_due()
    brief = ai.market_brief({"maerkte": []})
    assert brief["text"] == "## Lage\nRuhig." and brief["sources"] == [{"url": "https://news.example/a", "title": "Nachricht A"}]
    assert len(client.calls) == 2
    tool = client.calls[0]["tools"][0]
    assert tool["type"] == "web_search_20260209" and tool["max_uses"] == 5
    assert client.calls[1]["messages"][-1]["role"] == "assistant"  # pausierte Antwort wird fortgesetzt
    assert not ai.brief_due()  # heute schon erledigt
    assert ai.summary()["cost"]["calls"] == 2


def test_market_brief_without_web_search():
    ai, client = analyst([text_response("Text")], web_search=False)
    ai.market_brief({})
    assert client.calls[0]["tools"] == []
