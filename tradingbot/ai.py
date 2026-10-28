"""KI-Analyst: prüft Kaufsignale und schreibt eine tägliche Marktanalyse.

Anbieter: Google Gemini (Gratis-Kontingent, Standard wenn GEMINI_API_KEY gesetzt) oder
Anthropic Claude (kostenpflichtig, ANTHROPIC_API_KEY).

Grundsätze:
- Die KI kann einen Kauf nur VERHINDERN (Modus "veto") oder kommentieren ("advisory").
  Sie kann keine Käufe auslösen und keine Stops oder Verkäufe beeinflussen.
- Fällt die KI aus (Fehler, Ablehnung, Budget erschöpft), entscheiden die Regeln allein
  (einstellbar über ai.on_error).
- Jeder Aufruf wird mit geschätzten Kosten verbucht; ein Monatsbudget deckelt die Ausgaben.
"""
import json
import logging

import requests
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)

DEFAULTS = {
    "mode": "off",                 # off | advisory | veto
    "provider": "auto",            # auto (Gemini wenn Schlüssel da, sonst Claude) | gemini | claude
    "gemini_model": "gemini-3.8-flash",
    "gemini_fallback_model": "gemini-3.5-flash-lite",
    "model": "claude-opus-5-5",
    "effort_review": "medium",
    "effort_brief": "medium",
    "web_search": True,            # Websuche für die tägliche Marktanalyse
    "max_searches": 5,
    "brief_hour_utc": 6,           # ab dieser Stunde (UTC) einmal täglich neue Analyse
    "monthly_budget_usd": 20.0,
    "on_error": "approve",         # approve = Regeln entscheiden allein | reject = lieber nicht kaufen
    "price_input_per_mtok": 4.0,   # Claude Opus 5.5, Stand Okt. 2026
    "price_output_per_mtok": 20.0,
    "price_search_per_1k": 10.0,
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

REVIEW_SYSTEM = """Du bist der Risikomanager eines regelbasierten Trading-Bots (Trendfolge, nur Kauf \
und Verkauf, kein Hebel). Die Regeln des Bots sind über Jahre getestet; du bist eine zusätzliche \
Prüfung vor einem Kauf. Du bekommst das Kaufsignal mit Kennzahlen, die Gesamtmarktlage, das Portfolio \
und die aktuelle Marktanalyse des Tages.

Lehne einen Kauf nur mit einem konkreten, benennbaren Grund ab, zum Beispiel: ein wichtiges Ereignis \
in den nächsten Tagen laut Marktanalyse (Quartalszahlen, Notenbank-Entscheid, Wahl), der Kurs ist \
extrem überdehnt, das Portfolio ist schon stark im selben Thema investiert, oder Marktlage und Signal \
widersprechen sich deutlich. Ohne solchen Grund: genehmigen – dann entscheiden die Regeln.
Erfinde keine Fakten und keine Kursprognosen. Schreibe auf Deutsch, die Begründung in höchstens \
zwei Sätzen, verständlich für einen Privatanleger."""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "reject"]},
        "confidence": {"type": "string", "enum": ["niedrig", "mittel", "hoch"]},
        "reason": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["decision", "confidence", "reason", "risks"],
    "additionalProperties": False,
}

BRIEF_SYSTEM = """Du bist Marktanalyst für einen Privatanleger in Österreich, dessen Trading-Bot \
Aktien, ETFs, Rohstoffe, Devisen und Kryptowährungen beobachtet. Schreibe eine kurze Marktanalyse \
auf Deutsch (höchstens 350 Wörter, Markdown mit kurzen Abschnitten):
1. Gesamtlage in zwei, drei Sätzen.
2. Wichtige Termine der nächsten Tage (Notenbanken, Konjunkturdaten, Quartalszahlen der beobachteten \
Unternehmen).
3. Auffälligkeiten je Anlageklasse, bezogen auf die mitgelieferten Daten des Bots.
4. Größte Risiken.
Nutze die Websuche für aktuelle Nachrichten und nenne nur, was du gefunden hast oder was aus den \
Daten folgt. Keine Kaufempfehlungen, keine Kursziele."""


class GeminiError(Exception):
    pass


class Gemini:
    """Google Gemini über die Interactions-API (REST). Im Gratis-Kontingent ohne Kosten;
    bei Überschreiten der Limits schlägt die Anfrage fehl (HTTP 429), es wird nichts verrechnet."""

    def __init__(self, api_key: str, model: str, fallback_model: str = "", session=None, timeout: int = 120):
        self.api_key = api_key
        self.models = [m for m in (model, fallback_model) if m]
        self.session = session or requests.Session()
        self.timeout = timeout

    def _post(self, body: dict) -> dict:
        for i, model in enumerate(self.models):
            try:
                r = self.session.post(GEMINI_URL, json={**body, "model": model}, timeout=self.timeout,
                                      headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"})
            except requests.RequestException as e:
                raise GeminiError(f"Gemini nicht erreichbar ({e.__class__.__name__})") from None
            if r.status_code == 404 and i + 1 < len(self.models):
                log.warning("Gemini-Modell %s nicht verfügbar – versuche %s", model, self.models[i + 1])
                continue
            if r.status_code == 429:
                raise GeminiError("Gratis-Kontingent von Gemini gerade ausgeschöpft (Rate-Limit)")
            if r.status_code in (401, 403):
                raise GeminiError("Gemini-API-Schlüssel ungültig oder ohne Berechtigung (GEMINI_API_KEY prüfen)")
            if r.status_code >= 400:
                try:
                    msg = r.json().get("error", {}).get("message", "")
                except ValueError:
                    msg = r.text
                raise GeminiError(f"Gemini-Fehler {r.status_code}: {msg[:160]}")
            data = r.json()
            if data.get("status") == "failed":
                errors = "; ".join(e.get("message", "") for e in data.get("errors") or [])
                raise GeminiError(f"Gemini-Anfrage fehlgeschlagen: {errors[:160]}")
            data["_model"] = model
            return data
        raise GeminiError("Kein Gemini-Modell verfügbar")

    @staticmethod
    def parse(data: dict) -> tuple[str, dict]:
        """Antworttext und Quellen (URL -> Titel) aus einer Interaction."""
        texts, sources = [], {}
        for step in data.get("steps") or []:
            if step.get("type") != "model_output":
                continue
            for block in step.get("content") or []:
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                for a in block.get("annotations") or []:
                    if a.get("type") == "url_citation" and a.get("url"):
                        sources[a["url"]] = a.get("title") or a["url"]
        text = data["output_text"] if isinstance(data.get("output_text"), str) else "".join(texts)
        return text.strip(), sources

    def review(self, system: str, content: str, schema: dict) -> tuple[str, dict]:
        data = self._post({"system_instruction": system, "input": content, "store": False,
                           "response_format": {"type": "text", "mime_type": "application/json", "schema": schema}})
        return self.parse(data)[0], data

    def brief(self, system: str, content: str, search: bool) -> tuple[str, dict, dict]:
        body = {"system_instruction": system, "input": content, "store": False}
        if search:
            body["tools"] = [{"type": "google_search"}]
        data = self._post(body)
        text, sources = self.parse(data)
        return text, sources, data


# Gemini akzeptiert nur einen Teil von JSON-Schema: ohne additionalProperties
GEMINI_REVIEW_SCHEMA = {k: v for k, v in REVIEW_SCHEMA.items() if k != "additionalProperties"}


@dataclass
class Review:
    approve: bool
    decision: str           # approve | reject | error
    confidence: str = ""
    reason: str = ""
    risks: list = field(default_factory=list)
    error: str = ""
    cost_usd: float = 0.0


class AIAnalyst:
    def __init__(self, cfg: dict, api_key: str, state: dict, client=None, gemini_key: str = "", gemini=None):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.api_key = api_key
        self.gemini_key = gemini_key
        self.state = state.setdefault("ai", {})
        self.state.setdefault("decisions", [])
        self.state.setdefault("shadow", [])
        self._client = client
        self._gemini = gemini

    # ------------------------------------------------------------ Grundlagen
    @property
    def mode(self) -> str:
        return self.cfg["mode"] if self.cfg["mode"] in ("advisory", "veto") else "off"

    @property
    def provider(self) -> str:
        """gemini oder claude – bei 'auto' Gemini (kostenlos), sobald ein Gemini-Schlüssel da ist."""
        p = self.cfg.get("provider", "auto")
        if p in ("gemini", "claude"):
            return p
        has_gemini = bool(self.gemini_key or self._gemini)
        has_claude = bool(self.api_key or self._client)
        return "gemini" if has_gemini or not has_claude else "claude"

    @property
    def model_name(self) -> str:
        return self.cfg["gemini_model"] if self.provider == "gemini" else self.cfg["model"]

    @property
    def available(self) -> bool:
        if self.mode == "off":
            return False
        if self.provider == "gemini":
            return bool(self.gemini_key or self._gemini)
        return bool(self.api_key or self._client)

    def gemini(self) -> Gemini:
        if self._gemini is None:
            self._gemini = Gemini(self.gemini_key, self.cfg["gemini_model"], self.cfg["gemini_fallback_model"])
        return self._gemini

    def _book_gemini(self, data: dict) -> float:
        """Gemini im Gratis-Kontingent: keine Kosten, nur Aufrufe zählen."""
        month = self._month()
        month["calls"] += 1
        u = data.get("usage") or {}
        month["searches"] = month.get("searches", 0) + (u.get("grounding_tool_count") or 0)
        return 0.0

    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self.api_key, max_retries=2, timeout=180.0)
        return self._client

    def _month(self) -> dict:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        cost = self.state.get("cost")
        if not cost or cost.get("month") != month:
            cost = self.state["cost"] = {"month": month, "usd": 0.0, "calls": 0}
        return cost

    def budget_left(self) -> bool:
        return self._month()["usd"] < self.cfg["monthly_budget_usd"]

    def _book(self, response) -> float:
        """Geschätzte Kosten eines Aufrufs verbuchen (Listenpreise aus der Konfiguration)."""
        u = getattr(response, "usage", None)
        if u is None:
            return 0.0
        tokens_in = (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) * 1.25 \
            + (getattr(u, "cache_read_input_tokens", 0) or 0) * 0.1
        tokens_out = getattr(u, "output_tokens", 0) or 0
        stu = getattr(u, "server_tool_use", None)
        searches = (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
        usd = (tokens_in * self.cfg["price_input_per_mtok"] + tokens_out * self.cfg["price_output_per_mtok"]) / 1e6 \
            + searches * self.cfg["price_search_per_1k"] / 1000
        month = self._month()
        month["usd"] = round(month["usd"] + usd, 4)
        month["calls"] += 1
        return round(usd, 4)

    def _request(self, **kwargs):
        """Ein Aufruf mit serverseitigem Ersatzmodell, falls Claude die Anfrage ablehnt."""
        return self.client().beta.messages.create(
            model=self.cfg["model"], betas=[FALLBACK_BETA], fallbacks="default", **kwargs)

    def _fallback(self, reason: str) -> Review:
        approve = self.cfg["on_error"] != "reject"
        return Review(approve=approve, decision="error", reason=reason, error=reason)

    # ------------------------------------------------------------ Kaufsignal prüfen
    def review_entry(self, context: dict) -> Review:
        if not self.available:
            return Review(approve=True, decision="approve", reason="KI ausgeschaltet")
        if not self.budget_left():
            return self._fallback(f"Monatsbudget von {self.cfg['monthly_budget_usd']:.0f} $ erreicht")
        brief = (self.state.get("brief") or {}).get("text", "Keine Marktanalyse vorhanden.")
        content = ("Aktuelle Marktanalyse des Tages:\n" + brief + "\n\nKaufsignal des Bots (JSON):\n"
                   + json.dumps(context, ensure_ascii=False, indent=1))
        if self.provider == "gemini":
            try:
                text, data = self.gemini().review(REVIEW_SYSTEM, content, GEMINI_REVIEW_SCHEMA)
            except GeminiError as e:
                return self._fallback(str(e))
            return self._parse_review(text, self._book_gemini(data))
        try:
            import anthropic
        except ImportError:  # pragma: no cover - Paket fehlt
            return self._fallback("Python-Paket 'anthropic' fehlt")
        try:
            response = self._request(
                max_tokens=8000, system=REVIEW_SYSTEM,
                output_config={"effort": self.cfg["effort_review"],
                               "format": {"type": "json_schema", "schema": REVIEW_SCHEMA}},
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.AuthenticationError:
            return self._fallback("API-Schlüssel ungültig (ANTHROPIC_API_KEY prüfen)")
        except anthropic.RateLimitError:
            return self._fallback("KI vorübergehend überlastet (Rate-Limit)")
        except anthropic.APIStatusError as e:
            return self._fallback(f"KI-Fehler {e.status_code}")
        except anthropic.APIConnectionError:
            return self._fallback("KI nicht erreichbar (Netzwerk)")
        cost = self._book(response)
        if response.stop_reason == "refusal":
            return self._fallback("KI hat die Prüfung abgelehnt")
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        return self._parse_review(text, cost)

    def _parse_review(self, text: str, cost: float) -> Review:
        try:
            data = json.loads(text)
            decision = data["decision"]
            if decision not in ("approve", "reject"):
                raise ValueError(decision)
        except (ValueError, KeyError, TypeError):
            return self._fallback("KI-Antwort nicht lesbar")
        return Review(approve=decision == "approve", decision=decision, confidence=data.get("confidence", ""),
                      reason=str(data.get("reason", ""))[:600], risks=[str(r)[:200] for r in data.get("risks", [])][:5],
                      cost_usd=cost)

    def record(self, symbol: str, review: Review, enforced: bool, signal: dict) -> None:
        """Entscheidung protokollieren; abgelehnte Käufe als 'Schatten-Trade' weiterverfolgen."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.state["decisions"].append({"t": now, "symbol": symbol, "enforced": enforced, **asdict(review)})
        del self.state["decisions"][:-200]
        if review.decision == "reject":
            self.state["shadow"].append({
                "t": now, "symbol": symbol, "entry": signal["price"], "stop": signal["stop"],
                "tp": signal.get("tp"), "status": "offen", "result_pct": None, "reason": review.reason,
                "enforced": enforced,
            })
            del self.state["shadow"][:-200]

    def update_shadows(self, prices: dict[str, float], max_days: int = 14) -> None:
        """Wie wäre ein verhinderter Kauf ausgegangen? (gleiche Stop-/Ziel-Regeln)"""
        now = datetime.now(timezone.utc)
        for s in self.state["shadow"]:
            if s["status"] != "offen":
                continue
            price = prices.get(s["symbol"])
            if price is None:
                continue
            pct = round((price / s["entry"] - 1) * 100, 2)
            if price <= s["stop"]:
                s.update(status="wäre Verlust", result_pct=round((s["stop"] / s["entry"] - 1) * 100, 2))
            elif s.get("tp") and price >= s["tp"]:
                s.update(status="wäre Gewinn", result_pct=round((s["tp"] / s["entry"] - 1) * 100, 2))
            elif (now - datetime.fromisoformat(s["t"])).days >= max_days:
                s.update(status="abgelaufen", result_pct=pct)
            else:
                s["result_pct"] = pct

    # ------------------------------------------------------------ Tägliche Marktanalyse
    def brief_due(self) -> bool:
        if not self.available:
            return False
        now = datetime.now(timezone.utc)
        last = (self.state.get("brief") or {}).get("t", "")
        return now.hour >= self.cfg["brief_hour_utc"] and not last.startswith(now.strftime("%Y-%m-%d"))

    def market_brief(self, overview: dict) -> dict | None:
        if not self.available:
            return None
        if not self.budget_left():
            self.state["brief_error"] = "Monatsbudget erreicht – keine neue Analyse"
            return None
        if self.provider == "gemini":
            return self._gemini_brief(overview)
        import anthropic

        tools = []
        if self.cfg["web_search"]:
            tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": int(self.cfg["max_searches"])}]
        messages = [{"role": "user", "content": "Daten des Bots (JSON):\n" + json.dumps(overview, ensure_ascii=False)}]
        cost, response = 0.0, None
        try:
            for _ in range(4):  # lange Websuchen können pausieren (pause_turn) -> fortsetzen
                response = self._request(max_tokens=16000, system=BRIEF_SYSTEM, tools=tools,
                                         output_config={"effort": self.cfg["effort_brief"]}, messages=messages)
                cost += self._book(response)
                if response.stop_reason != "pause_turn":
                    break
                messages.append({"role": "assistant", "content": response.content})
        except anthropic.APIError as e:
            self.state["brief_error"] = f"Marktanalyse fehlgeschlagen: {e.__class__.__name__}"
            log.warning(self.state["brief_error"])
            return None
        if response is None or response.stop_reason == "refusal":
            self.state["brief_error"] = "KI hat die Marktanalyse abgelehnt"
            return None
        text, sources = [], {}
        for block in response.content:
            if getattr(block, "type", "") == "text":
                text.append(block.text)
                for c in getattr(block, "citations", None) or []:
                    url = getattr(c, "url", None)
                    if url:
                        sources[url] = getattr(c, "title", "") or url
        brief = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "text": "".join(text).strip(),
                 "sources": [{"url": u, "title": t} for u, t in list(sources.items())[:12]],
                 "model": getattr(response, "model", self.cfg["model"]), "cost_usd": round(cost, 4)}
        self.state["brief"] = brief
        self.state["brief_error"] = ""
        return brief

    def _gemini_brief(self, overview: dict) -> dict | None:
        content = "Daten des Bots (JSON):\n" + json.dumps(overview, ensure_ascii=False)
        try:
            text, sources, data = self.gemini().brief(BRIEF_SYSTEM, content, bool(self.cfg["web_search"]))
        except GeminiError as e:
            self.state["brief_error"] = f"Marktanalyse fehlgeschlagen: {e}"
            log.warning(self.state["brief_error"])
            return None
        self._book_gemini(data)
        if not text:
            self.state["brief_error"] = "Gemini hat keine Analyse geliefert"
            return None
        brief = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "text": text,
                 "sources": [{"url": u, "title": t} for u, t in list(sources.items())[:12]],
                 "model": data.get("_model", self.cfg["gemini_model"]), "cost_usd": 0.0}
        self.state["brief"] = brief
        self.state["brief_error"] = ""
        return brief

    def summary(self) -> dict:
        """Kennzahlen für das Dashboard (auch die 'KI-Bilanz' der verhinderten Käufe)."""
        shadows = self.state["shadow"]
        done = [s for s in shadows if s["status"] != "offen"]
        return {
            "mode": self.mode, "available": self.available, "model": self.model_name, "provider": self.provider,
            "free": self.provider == "gemini",
            "budget_usd": self.cfg["monthly_budget_usd"], "cost": self._month(),
            "brief": self.state.get("brief"), "brief_error": self.state.get("brief_error", ""),
            "decisions": self.state["decisions"][-40:][::-1],
            "shadow": shadows[-40:][::-1],
            "bilanz": {
                "rejected": len(shadows),
                "would_lose": sum(1 for s in done if (s["result_pct"] or 0) < 0),
                "would_win": sum(1 for s in done if (s["result_pct"] or 0) > 0),
                "open": len(shadows) - len(done),
                "avoided_pct": round(-sum(s["result_pct"] or 0 for s in done), 2),
            },
        }
