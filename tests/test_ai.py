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


# ------------------------------------------------------------ Google Gemini (kostenlos)
class FakeResponse:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data
        self.text = json.dumps(data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def interaction(text, citations=None, searches=0):
    annotations = [{"type": "url_citation", "url": u, "title": t} for u, t in (citations or [])]
    return {"id": "i1", "status": "completed",
            "steps": [{"type": "google_search_call"}, {"type": "model_output",
                                                       "content": [{"type": "text", "text": text, "annotations": annotations}]}],
            "usage": {"total_input_tokens": 900, "total_output_tokens": 120, "grounding_tool_count": searches}}


def gemini_analyst(responses, **cfg):
    from tradingbot.ai import Gemini

    session = FakeSession(responses)
    g = Gemini("g-key", "gemini-3.8-flash", "gemini-3.5-flash-lite", session=session)
    return AIAnalyst({"mode": "veto", **cfg}, "", {}, gemini_key="g-key", gemini=g), session


def test_provider_selection():
    assert AIAnalyst({"mode": "veto"}, "", {}, gemini_key="g").provider == "gemini"
    assert AIAnalyst({"mode": "veto"}, "a", {}).provider == "claude"
    assert AIAnalyst({"mode": "veto"}, "a", {}, gemini_key="g").provider == "gemini"   # kostenlos bevorzugt
    assert AIAnalyst({"mode": "veto", "provider": "claude"}, "a", {}, gemini_key="g").provider == "claude"
    assert AIAnalyst({"mode": "veto"}, "", {}, gemini_key="g").available
    assert not AIAnalyst({"mode": "off"}, "", {}, gemini_key="g").available


def test_gemini_review_request_and_parse():
    ai, session = gemini_analyst([FakeResponse(200, interaction(json.dumps(
        {"decision": "reject", "confidence": "hoch", "reason": "Zinsentscheid morgen.", "risks": ["Fed"]})))])
    review = ai.review_entry(CONTEXT)
    assert review.decision == "reject" and not review.approve and review.reason == "Zinsentscheid morgen."
    assert review.cost_usd == 0.0
    call = session.calls[0]
    assert call["url"].endswith("/v1beta/interactions") and call["headers"]["x-goog-api-key"] == "g-key"
    body = call["json"]
    assert body["model"] == "gemini-3.8-flash" and body["store"] is False and "system_instruction" in body
    assert body["response_format"]["mime_type"] == "application/json"
    assert "additionalProperties" not in body["response_format"]["schema"]
    assert "AAPL" in body["input"] and "tools" not in body
    assert ai.summary()["cost"]["calls"] == 1 and ai.summary()["free"]


def test_gemini_falls_back_to_lite_model_on_404():
    ai, session = gemini_analyst([FakeResponse(404, {"error": {"message": "model not found"}}),
                                  FakeResponse(200, interaction('{"decision":"approve","confidence":"mittel","reason":"ok","risks":[]}'))])
    assert ai.review_entry(CONTEXT).decision == "approve"
    assert [c["json"]["model"] for c in session.calls] == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


@pytest.mark.parametrize("response, text", [
    (FakeResponse(429, {"error": {"message": "quota"}}), "Gratis-Kontingent"),
    (FakeResponse(403, {"error": {"message": "denied"}}), "GEMINI_API_KEY"),
    (FakeResponse(500, {"error": {"message": "boom"}}), "Gemini-Fehler 500: boom"),
    (FakeResponse(200, {"status": "failed", "errors": [{"message": "kaputt"}]}), "kaputt"),
    (FakeResponse(200, interaction("kein json")), "nicht lesbar"),
])
def test_gemini_errors_fall_back_to_rules(response, text):
    ai, _ = gemini_analyst([response])
    review = ai.review_entry(CONTEXT)
    assert review.decision == "error" and review.approve and text in review.reason


def test_gemini_network_error():
    import requests

    ai, _ = gemini_analyst([requests.ConnectionError("weg")])
    assert "nicht erreichbar" in ai.review_entry(CONTEXT).reason


def test_gemini_brief_with_google_search():
    ai, session = gemini_analyst([FakeResponse(200, interaction(
        "## Lage\nRuhig.", citations=[("https://news.example/x", "Meldung X")], searches=3))], brief_hour_utc=0)
    brief = ai.market_brief({"maerkte": []})
    assert brief["text"] == "## Lage\nRuhig." and brief["sources"] == [{"url": "https://news.example/x", "title": "Meldung X"}]
    assert brief["cost_usd"] == 0.0 and brief["model"] == "gemini-3.8-flash"
    assert session.calls[0]["json"]["tools"] == [{"type": "google_search"}]
    assert ai.summary()["cost"]["searches"] == 3


def test_gemini_brief_error_is_reported():
    ai, _ = gemini_analyst([FakeResponse(429, {})], brief_hour_utc=0)
    assert ai.market_brief({}) is None and "Gratis-Kontingent" in ai.state["brief_error"]
