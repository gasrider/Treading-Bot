"""Web-Dashboard: JSON-API + Single-Page-App (tradingbot/web), Anmeldung, Watchdog.

Läuft als eigener Prozess/Container neben dem Bot. Es liest die State-Datei und das
Log des Bots und steuert ihn ausschließlich über Dateien in state/ (siehe control.py).
Börsen-Schlüssel braucht es nicht: Chart-Daten holt es über die öffentliche Börsen-API.

Sicherheit: Anmeldung per signiertem Sitzungs-Cookie (HttpOnly, SameSite=Strict, Secure
hinter HTTPS) oder HTTP-Basic-Auth (für Skripte). Ändernde Aufrufe brauchen zusätzlich
den Header X-Requested-With und dieselbe Herkunft. Strenge Content-Security-Policy.
"""
import base64
import copy
import csv
import hashlib
import hmac
import io
import json
import logging
import mimetypes
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import yaml

from . import config as config_module
from . import control
from .analytics import benchmark_return, max_drawdown, trade_stats
from .config import EDITABLE, TIMEFRAMES, apply_overrides, load_config, validate
from .data import make_exchange, to_frame
from .notifier import Notifier
from .state import StateStore
from .strategy import add_indicators

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
STALE_SECONDS = 15 * 60          # so lange ohne Lebenszeichen = Bot gilt als ausgefallen
LOG_FILE = Path("logs") / "bot.log"
SECRET_FILE = Path("state") / ".dashboard_secret"
MAX_FAILED_LOGINS = 10
LOCKOUT_SECONDS = 15 * 60
MAX_BODY_BYTES = 20_000
SESSION_DAYS = 30
CHART_CACHE_SECONDS = 20
COMMON_PAIRS = ["BTC", "ETH", "SOL", "XRP", "ADA", "DOT", "LINK", "LTC", "AVAX", "DOGE"]
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,10}/[A-Z]{3,4}$")
TF_SECONDS = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}

HELP = {
    "ema_fast": "Reagiert schnell auf Kursänderungen. Kreuzt sie die langsame EMA nach oben, ist das das Kaufsignal.",
    "ema_slow": "Glättet den Kurs stärker. Bildet mit der schnellen EMA das Kreuzungssignal.",
    "ema_trend": "Langfristiger Trend. Nur wenn der Kurs darüber liegt, kauft der Bot.",
    "rsi_max_entry": "Ist der RSI höher, gilt der Markt als überkauft – dann kein Kauf.",
    "stop_atr": "Abstand des Stop-Loss in Vielfachen der durchschnittlichen Schwankung (ATR).",
    "take_profit_atr": "Gewinnziel in ATR. 0 = kein festes Ziel, nur Trailing-Stop und Trendwende.",
    "trailing_atr": "Zieht den Stop hinter dem Höchstkurs nach und sichert so Gewinne. 0 = aus.",
    "risk_per_trade": "So viel vom Kapital darf ein einzelner Trade höchstens verlieren, wenn der Stop greift.",
    "max_position_pct": "Obergrenze für den Wert einer einzelnen Position.",
    "max_open_positions": "Wie viele Paare gleichzeitig gehalten werden dürfen.",
    "max_daily_loss": "Fällt das Kapital an einem Tag um so viel, kauft der Bot bis morgen nichts mehr.",
    "max_drawdown": "Notbremse: Fällt das Kapital so weit unter den Höchststand, verkauft der Bot alles und stoppt.",
}


# ---------------------------------------------------------------- Zustand
def heartbeat_age(state: dict, now: datetime | None = None) -> float | None:
    hb = state.get("heartbeat")
    if not hb:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - datetime.fromisoformat(hb)).total_seconds()


def bot_health(state: dict) -> tuple[str, str]:
    """Liefert (Status-Schlüssel, Text) für die Anzeige."""
    age = heartbeat_age(state)
    if age is None:
        return "unknown", "Noch kein Lebenszeichen"
    if age > STALE_SECONDS:
        return "down", f"Keine Antwort seit {int(age // 60)} Min."
    if state.get("halted"):
        return "halted", "Gestoppt – " + state.get("halt_reason", "")
    if state.get("paused"):
        return "paused", "Pausiert – keine neuen Käufe"
    if state.get("last_error"):
        return "warn", "Läuft, letzter Durchlauf mit Fehler"
    return "ok", "Läuft"


def market_status(m: dict, position: dict | None, strategy: dict) -> tuple[str, str]:
    """Erklärt in Alltagssprache, was der Bot gerade tut bzw. worauf er wartet."""
    if m.get("error"):
        return "bad", "Fehler: " + m["error"]
    if position:
        return "ok", "Position offen – der Bot überwacht Stop und Ziel."
    if m.get("entry_signal"):
        return "ok", "Kaufsignal auf der letzten Kerze."
    if not m.get("uptrend"):
        return "muted", f"Kein Aufwärtstrend – Kurs unter der EMA {strategy['ema_trend']}. Der Bot kauft nicht."
    if m.get("fast_above_slow"):
        return "warn", "Aufwärtstrend läuft bereits – der Bot kauft nicht hinterher, sondern wartet auf das nächste Signal."
    text = f"Aufwärtstrend – der Bot wartet, bis die EMA {strategy['ema_fast']} die EMA {strategy['ema_slow']} nach oben kreuzt."
    if not m.get("rsi_ok"):
        text += " RSI derzeit zu hoch."
    return "warn", text


def next_evaluation(state: dict, timeframe: str) -> str | None:
    """Zeitpunkt, an dem die nächste Kerze schließt und der Bot neu entscheidet."""
    stamps = [v for v in state.get("last_bar", {}).values() if v]
    if not stamps:
        return None
    last_open = max(pd.Timestamp(v) for v in stamps)
    step = timedelta(seconds=TF_SECONDS.get(timeframe, 3600))
    nxt = last_open.to_pydatetime() + 2 * step
    now = datetime.now(timezone.utc)
    while nxt < now:
        nxt += step
    return nxt.isoformat(timespec="seconds")


def status_payload(cfg: dict, state: dict) -> dict:
    status, status_text = bot_health(state)
    market = state.get("market", {})
    prices = {s: m.get("price") for s, m in market.items() if m.get("price")}
    history = state.get("equity_history", [])
    equity = state.get("equity", history[-1][1] if history else None)
    day_start = state.get("day_start_equity")
    benchmark = state.get("benchmark")
    start_equity = (benchmark or {}).get("equity") or (history[0][1] if history else None)
    positions = []
    for sym, p in state.get("positions", {}).items():
        price = prices.get(sym, p["entry"])
        value = p["qty"] * price
        upnl = value - p["cost"]
        span = (p["tp"] - p["stop"]) if p.get("tp") else None
        positions.append({
            "symbol": sym, "qty": p["qty"], "entry": p["entry"], "stop": p["stop"], "tp": p.get("tp"),
            "price": price, "value": round(value, 2), "upnl": round(upnl, 2),
            "upnl_pct": round(upnl / p["cost"] * 100, 2) if p["cost"] else 0,
            "stop_dist_pct": round((p["stop"] / price - 1) * 100, 2),
            "tp_dist_pct": round((p["tp"] / price - 1) * 100, 2) if p.get("tp") else None,
            "progress": max(0.0, min(1.0, (price - p["stop"]) / span)) if span else None,
            "opened_at": p["opened_at"],
        })
    markets = []
    for sym in cfg["symbols"] + [s for s in market if s not in cfg["symbols"]]:
        m = market.get(sym)
        if not m or not m.get("candles"):
            markets.append({"symbol": sym, "pending": True, "error": (m or {}).get("error", "")})
            continue
        tone, text = market_status(m, state.get("positions", {}).get(sym), cfg["strategy"])
        closes = [c[4] for c in m["candles"]]
        markets.append({
            "symbol": sym, "price": m.get("price"), "rsi": m.get("rsi"),
            "change_pct": round((closes[-1] / m["candles"][0][1] - 1) * 100, 2),
            "change_span_hours": len(closes) * TF_SECONDS.get(m.get("timeframe", "1h"), 3600) / 3600,
            "spark": closes, "uptrend": m.get("uptrend"), "fast_above_slow": m.get("fast_above_slow"),
            "rsi_ok": m.get("rsi_ok"), "entry_signal": m.get("entry_signal"), "tone": tone, "text": text,
            "updated": m.get("updated"), "error": m.get("error", ""),
        })
    trades = state.get("closed_trades", [])
    upnl_total = sum(p["upnl"] for p in positions)
    return {
        "bot": {
            "status": status, "status_text": status_text, "heartbeat": state.get("heartbeat"),
            "mode": cfg["mode"], "exchange": cfg["exchange"], "halted": bool(state.get("halted")),
            "halt_reason": state.get("halt_reason", ""), "paused": control.PAUSE_FILE.exists(),
            "stop_requested": control.KILL_SWITCH.exists(), "last_error": state.get("last_error", ""),
            "next_eval": next_evaluation(state, cfg["timeframe"]), "timeframe": cfg["timeframe"],
            "symbols": cfg["symbols"], "quote": cfg["quote"],
        },
        "account": {
            "equity": equity, "cash": state.get("paper_cash"), "peak": state.get("peak_equity"),
            "today_pnl": round(equity - day_start, 2) if equity is not None and day_start else None,
            "today_pct": round((equity / day_start - 1) * 100, 2) if equity is not None and day_start else None,
            "total_pct": round((equity / start_equity - 1) * 100, 2) if equity and start_equity else None,
            "benchmark_pct": benchmark_return(benchmark, prices),
            "since": (benchmark or {}).get("time") or (history[0][0] if history else None),
            "max_dd_pct": max_drawdown(history), "unrealized": round(upnl_total, 2),
            "realized": round(sum(t["pnl"] for t in trades), 2),
            "risk_per_trade_eur": round(equity * cfg["risk"]["risk_per_trade"], 2) if equity else None,
        },
        "positions": positions,
        "markets": markets,
        "strategy": cfg["strategy"],
        "risk": cfg["risk"],
        "stats": trade_stats(trades),
        "equity_history": history[-24 * 90:],
        "events": state.get("events", [])[-60:][::-1],
    }


def tail(path: Path, lines: int = 200) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return list(deque(f, maxlen=lines))


# ---------------------------------------------------------------- Chart-Daten
class ChartProvider:
    """Holt Kerzen über die öffentliche Börsen-API (mit kurzem Cache) und berechnet die
    Indikatoren mit genau derselben Funktion wie der Bot."""

    def __init__(self, exchange_factory=make_exchange):
        self.exchange_factory = exchange_factory
        self.exchanges = {}
        self.cache = {}
        self.lock = threading.Lock()

    def _candles(self, exchange_id: str, symbol: str, tf: str) -> pd.DataFrame:
        key = (exchange_id, symbol, tf)
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.time() - hit[0] < CHART_CACHE_SECONDS:
                return hit[1]
            ex = self.exchanges.get(exchange_id) or self.exchange_factory(exchange_id)
            self.exchanges[exchange_id] = ex
        df = to_frame(ex.fetch_ohlcv(symbol, tf, limit=720))
        with self.lock:
            self.cache[key] = (time.time(), df)
        return df

    def chart(self, cfg: dict, state: dict, symbol: str, tf: str) -> dict:
        try:
            df = self._candles(cfg["exchange"], symbol, tf)
            source = "exchange"
        except Exception as e:  # Börse nicht erreichbar -> Momentaufnahme des Bots zeigen
            log.warning("Chart-Daten für %s nicht abrufbar: %s", symbol, e)
            snap = state.get("market", {}).get(symbol, {})
            if not snap.get("candles"):
                raise
            df = pd.DataFrame(
                [{"open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": 0} for c in snap["candles"]],
                index=pd.to_datetime([c[0] for c in snap["candles"]], utc=True),
            )
            tf, source = snap.get("timeframe", tf), "snapshot"
        data = add_indicators(df, cfg["strategy"])
        times = [int(ts.timestamp()) for ts in data.index]

        def series(col, digits=8):
            return [{"time": t, "value": round(float(v), digits)} for t, v in zip(times, data[col]) if pd.notna(v)]

        step = TF_SECONDS.get(tf, 3600)

        def bar_time(iso: str) -> int | None:
            ts = int(datetime.fromisoformat(iso).timestamp())
            ts -= ts % step
            return ts if times and times[0] <= ts <= times[-1] else None

        markers = []
        for t in state.get("closed_trades", []):
            if t["symbol"] != symbol:
                continue
            if (bt := bar_time(t["opened_at"])) is not None:
                markers.append({"time": bt, "position": "belowBar", "color": "#16a34a", "shape": "arrowUp", "text": "Kauf"})
            if (bt := bar_time(t["closed_at"])) is not None:
                markers.append({"time": bt, "position": "aboveBar", "color": "#16a34a" if t["pnl"] > 0 else "#dc2626",
                                "shape": "arrowDown", "text": f"{t['pnl']:+.2f}"})
        pos = state.get("positions", {}).get(symbol)
        if pos and (bt := bar_time(pos["opened_at"])) is not None:
            markers.append({"time": bt, "position": "belowBar", "color": "#2563eb", "shape": "arrowUp", "text": "Offen"})
        markers.sort(key=lambda m: m["time"])
        return {
            "symbol": symbol, "timeframe": tf, "source": source,
            "candles": [{"time": t, "open": float(o), "high": float(h), "low": float(lo), "close": float(c)}
                        for t, o, h, lo, c in zip(times, data["open"], data["high"], data["low"], data["close"])],
            "ema_fast": series("ema_fast"), "ema_slow": series("ema_slow"), "ema_trend": series("ema_trend"),
            "rsi": series("rsi", 2),
            "markers": markers,
            "levels": {"entry": pos["entry"], "stop": pos["stop"], "tp": pos.get("tp")} if pos else None,
            "params": cfg["strategy"],
        }


# ---------------------------------------------------------------- Einstellungen
def settings_payload(cfg: dict, equity: float | None) -> dict:
    fields = [
        {"section": sec, "key": key, "label": label, "int": typ is int, "min": lo, "max": hi, "pct": pct,
         "value": cfg[sec][key], "help": HELP.get(key, "")}
        for sec, key, label, typ, lo, hi, pct in EDITABLE
    ]
    return {
        "symbols": cfg["symbols"], "timeframe": cfg["timeframe"], "timeframes": list(TIMEFRAMES),
        "pairs": list(dict.fromkeys([f"{b}/{cfg['quote']}" for b in COMMON_PAIRS] + cfg["symbols"])),
        "quote": cfg["quote"], "mode": cfg["mode"], "exchange": cfg["exchange"], "fields": fields,
        "override_active": config_module.SETTINGS_OVERRIDE.exists(), "equity": equity,
    }


def parse_settings_json(body: dict) -> dict:
    """JSON aus dem Einstellungsformular → Overrides. Wirft ValueError bei ungültiger Eingabe."""
    symbols = []
    for raw in body.get("symbols") or []:
        sym = str(raw).strip().upper()
        if not SYMBOL_RE.match(sym):
            raise ValueError(f"Ungültiges Paar „{str(raw)[:20]}“ – Format z. B. ATOM/EUR")
        symbols.append(sym)
    overrides = {"symbols": list(dict.fromkeys(symbols)), "timeframe": str(body.get("timeframe", "")),
                 "strategy": {}, "risk": {}}
    for section, key, label, typ, *_ in EDITABLE:
        raw = (body.get(section) or {}).get(key)
        try:
            value = float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            raise ValueError(f"{label}: bitte eine Zahl eingeben") from None
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{label}: bitte eine Zahl eingeben")
        overrides[section][key] = int(round(value)) if typ is int else round(value, 6)
    return overrides


def save_overrides(overrides: dict) -> None:
    path = config_module.SETTINGS_OVERRIDE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("# Im Dashboard gespeichert – überschreibt config.yaml. Löschen = zurücksetzen.\n"
                   + yaml.safe_dump(overrides, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


def trades_csv(trades: list[dict]) -> bytes:
    """Excel-freundlich für den deutschsprachigen Raum: Semikolon, Dezimalkomma, UTF-8 mit BOM."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Eröffnet (UTC)", "Geschlossen (UTC)", "Paar", "Menge", "Kaufkurs", "Verkaufskurs",
                     "Ergebnis", "Grund"])
    for t in trades:
        writer.writerow([t.get("opened_at", "")[:19].replace("T", " "), t.get("closed_at", "")[:19].replace("T", " "),
                         t["symbol"], *(str(t[k]).replace(".", ",") for k in ("qty", "entry", "exit", "pnl")),
                         t["reason"]])
    return ("﻿" + out.getvalue()).encode("utf-8")


# ---------------------------------------------------------------- Anmeldung
def load_secret() -> bytes:
    """Zufälliger Schlüssel für Sitzungs-Cookies; bleibt über Neustarts/Updates erhalten."""
    try:
        return bytes.fromhex(SECRET_FILE.read_text().strip())
    except (OSError, ValueError):
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        SECRET_FILE.write_text(key.hex())
        SECRET_FILE.chmod(0o600)
        return key


class Sessions:
    def __init__(self, secret: bytes, user: str, password: str):
        # Passwort fließt in den Schlüssel ein: Passwort ändern = alle Sitzungen ungültig
        self.key = hmac.new(secret, hashlib.sha256(f"{user}:{password}".encode()).digest(), "sha256").digest()

    def issue(self, days: int = SESSION_DAYS) -> str:
        expires = int(time.time()) + days * 86400
        sig = hmac.new(self.key, str(expires).encode(), "sha256").hexdigest()
        return f"{expires}.{sig}"

    def valid(self, token: str) -> bool:
        try:
            expires, sig = token.split(".", 1)
            good = hmac.new(self.key, expires.encode(), "sha256").hexdigest()
            return hmac.compare_digest(sig, good) and int(expires) > time.time()
        except ValueError:
            return False


class LoginLimiter:
    def __init__(self):
        self.failures = defaultdict(list)
        self.lock = threading.Lock()

    def blocked(self, ip: str) -> bool:
        with self.lock:
            now = time.time()
            self.failures[ip] = [t for t in self.failures[ip] if now - t < LOCKOUT_SECONDS]
            return len(self.failures[ip]) >= MAX_FAILED_LOGINS

    def fail(self, ip: str) -> None:
        with self.lock:
            self.failures[ip].append(time.time())


# ---------------------------------------------------------------- Watchdog
class Watchdog(threading.Thread):
    """Meldet per Telegram, wenn der Bot kein Lebenszeichen mehr gibt (und wenn er zurück ist)."""

    def __init__(self, store: StateStore, notifier: Notifier, interval: int = 60):
        super().__init__(daemon=True)
        self.store = store
        self.notifier = notifier
        self.interval = interval
        self.alerted = False

    def check(self) -> None:
        age = heartbeat_age(self.store.load())
        if age is None:
            return
        if age > STALE_SECONDS and not self.alerted:
            self.notifier.send(
                f"🚨 Bot gibt seit {int(age // 60)} Minuten kein Lebenszeichen! Server/Container prüfen."
            )
            self.alerted = True
        elif age <= STALE_SECONDS and self.alerted:
            self.notifier.send("💚 Bot läuft wieder.")
            self.alerted = False

    def run(self) -> None:
        while True:
            try:
                self.check()
            except Exception:
                log.exception("Watchdog-Fehler")
            time.sleep(self.interval)


# ---------------------------------------------------------------- HTTP
# Der Hash erlaubt genau einen <style>-Block: den, den die Chart-Bibliothek für ihr
# TradingView-Logo einfügt (Lizenzhinweis). Sonst sind keine Inline-Styles/-Skripte erlaubt.
LWC_LOGO_STYLE = "'sha256-3pRED1tOXas1FXFoPb9TGCjmYe9XQsmO9OV23khV2nY='"
CSP = (f"default-src 'none'; script-src 'self'; style-src 'self' {LWC_LOGO_STYLE}; img-src 'self' data:; "
       "connect-src 'self'; manifest-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


def make_handler(cfg_loader, store: StateStore, user: str, password: str, secret: bytes,
                 charts: ChartProvider | None = None):
    """cfg_loader: Funktion, die die aktuelle Konfiguration liefert (inkl. Dashboard-Einstellungen)."""
    limiter = LoginLimiter()
    sessions = Sessions(secret, user, password)
    charts = charts or ChartProvider()
    basic = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    static_files = {p.relative_to(WEB_DIR).as_posix(): p for p in WEB_DIR.rglob("*") if p.is_file()}

    class Handler(BaseHTTPRequestHandler):
        server_version = "TradingBotDashboard"

        def log_message(self, fmt, *args):
            log.info("%s %s", self.client_ip(), fmt % args)

        def client_ip(self) -> str:
            forwarded = self.headers.get("X-Forwarded-For", "")
            return forwarded.split(",")[0].strip() or self.client_address[0]

        # ------------------------------------------------ Antworten
        def send(self, code: int, body: bytes, ctype: str, extra=None, cache: str = "no-store"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")  # Formulare/fetch senden ihre echte Herkunft
            self.send_header("Content-Security-Policy", CSP)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def json(self, data, code: int = 200, extra=None):
            self.send(code, json.dumps(data, default=str).encode(), "application/json; charset=utf-8", extra)

        def error(self, code: int, message: str):
            self.json({"error": message}, code)

        # ------------------------------------------------ Anmeldung
        def session_cookie(self, value: str, max_age: int) -> str:
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            return f"tb_session={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

        def logged_in(self) -> bool:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            if "tb_session" in cookie and sessions.valid(cookie["tb_session"].value):
                return True
            auth = self.headers.get("Authorization", "")
            if auth:
                ip = self.client_ip()
                if limiter.blocked(ip):
                    return False
                if hmac.compare_digest(auth, basic):
                    return True
                limiter.fail(ip)
            return False

        def write_allowed(self) -> bool:
            """Schutz gegen fremde Webseiten (CSRF): eigener Header + gleiche Herkunft."""
            if self.headers.get("X-Requested-With") != "tradingbot":
                return False
            origin = self.headers.get("Origin") or self.headers.get("Referer")
            return not origin or urlparse(origin).netloc == self.headers.get("Host")

        def read_json(self) -> dict | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self.error(413, "Anfrage zu groß")
                return None
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self.error(400, "Ungültige Anfrage")
                return None
            if not isinstance(data, dict):
                self.error(400, "Ungültige Anfrage")
                return None
            return data

        # ------------------------------------------------ GET
        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            if path == "/health":
                self.send(200, b"ok", "text/plain")
                return
            if path in ("/", "/index.html"):
                self.send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8", cache="no-cache")
                return
            if path.startswith("/static/"):
                file = static_files.get(path[len("/static/"):])
                if not file:
                    self.send(404, b"Nicht gefunden", "text/plain")
                    return
                ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                if file.suffix == ".js":
                    ctype = "text/javascript"
                elif file.suffix == ".webmanifest":
                    ctype = "application/manifest+json"
                if ctype.startswith("text/"):
                    ctype += "; charset=utf-8"
                self.send(200, file.read_bytes(), ctype, cache="no-cache")
                return
            if path == "/api/session":
                self.json({"authenticated": self.logged_in(), "user": user})
                return
            if not path.startswith("/api/"):
                self.send(404, b"Nicht gefunden", "text/plain")
                return
            if not self.logged_in():
                self.error(401, "Bitte anmelden")
                return
            cfg = cfg_loader()
            state = store.load()
            query = parse_qs(url.query)
            if path == "/api/status":
                self.json(status_payload(cfg, state))
            elif path == "/api/chart":
                symbol = query.get("symbol", [cfg["symbols"][0]])[0].upper()
                tf = query.get("tf", [cfg["timeframe"]])[0]
                if not SYMBOL_RE.match(symbol) or tf not in TIMEFRAMES:
                    self.error(400, "Ungültiges Paar oder Intervall")
                    return
                try:
                    self.json(charts.chart(cfg, state, symbol, tf))
                except Exception as e:
                    self.error(502, f"Kursdaten nicht verfügbar: {e}")
            elif path == "/api/trades":
                trades = state.get("closed_trades", [])
                self.json({"trades": trades[::-1], "stats": trade_stats(trades), "quote": cfg["quote"]})
            elif path == "/api/trades.csv":
                name = f"trades_{datetime.now():%Y-%m-%d}.csv"
                self.send(200, trades_csv(state.get("closed_trades", [])), "text/csv; charset=utf-8",
                          {"Content-Disposition": f'attachment; filename="{name}"'})
            elif path == "/api/settings":
                self.json(settings_payload(cfg, state.get("equity")))
            elif path == "/api/logs":
                try:
                    n = max(1, min(int(query.get("n", ["200"])[0]), 1000))
                except ValueError:
                    n = 200
                self.json({"lines": [line.rstrip("\n") for line in tail(LOG_FILE, n)]})
            else:
                self.error(404, "Nicht gefunden")

        do_HEAD = do_GET

        # ------------------------------------------------ POST
        def do_POST(self):
            path = urlparse(self.path).path
            if not self.write_allowed():
                self.error(403, "Anfrage abgelehnt (Herkunft)")
                return
            if path == "/api/login":
                self.login()
                return
            if path == "/api/logout":
                self.json({"ok": True}, extra={"Set-Cookie": self.session_cookie("", 0)})
                return
            if not self.logged_in():
                self.error(401, "Bitte anmelden")
                return
            body = self.read_json()
            if body is None:
                return
            if path == "/api/control":
                self.control(body)
            elif path == "/api/settings":
                self.save_settings(body)
            elif path == "/api/settings/reset":
                config_module.SETTINGS_OVERRIDE.unlink(missing_ok=True)
                log.warning("Einstellungen zurückgesetzt über Dashboard von %s", self.client_ip())
                self.json({"ok": True, "message": "Zurückgesetzt – es gelten wieder die Werte aus config.yaml."})
            else:
                self.error(404, "Nicht gefunden")

        def login(self):
            ip = self.client_ip()
            if limiter.blocked(ip):
                self.error(429, "Zu viele Fehlversuche – bitte 15 Minuten warten.")
                return
            body = self.read_json()
            if body is None:
                return
            ok_user = hmac.compare_digest(str(body.get("user", "")).encode(), user.encode())
            ok_pass = hmac.compare_digest(str(body.get("password", "")).encode(), password.encode())
            if not (ok_user and ok_pass):
                limiter.fail(ip)
                log.warning("Fehlgeschlagene Anmeldung von %s", ip)
                self.error(401, "Benutzer oder Passwort falsch")
                return
            days = SESSION_DAYS if body.get("remember") else 1
            log.info("Anmeldung von %s", ip)
            self.json({"ok": True}, extra={"Set-Cookie": self.session_cookie(sessions.issue(days), days * 86400)})

        def control(self, body: dict):
            action = body.get("action")
            control.STATE_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).isoformat()
            if action == "stop":
                control.KILL_SWITCH.write_text(stamp)
                message = "Not-Aus ausgelöst – der Bot verkauft alles beim nächsten Durchlauf."
            elif action == "resume":
                control.RESUME_FILE.write_text(stamp)
                message = "Freigabe angefordert – der Bot handelt ab dem nächsten Durchlauf wieder."
            elif action == "pause":
                control.PAUSE_FILE.write_text(stamp)
                message = "Pausiert – keine neuen Käufe, Stops bleiben aktiv."
            elif action == "unpause":
                control.PAUSE_FILE.unlink(missing_ok=True)
                message = "Pause beendet."
            elif action == "close":
                symbol = str(body.get("symbol", "")).upper()
                if symbol not in store.load().get("positions", {}):
                    self.error(400, "Keine offene Position in diesem Paar")
                    return
                control.queue_command("close", symbol=symbol)
                message = f"{symbol} wird beim nächsten Durchlauf verkauft."
            else:
                self.error(400, "Unbekannte Aktion")
                return
            log.warning("Steuerung '%s' über Dashboard von %s", action, self.client_ip())
            self.json({"ok": True, "message": message})

        def save_settings(self, body: dict):
            cfg = cfg_loader()
            try:
                overrides = parse_settings_json(body)
                validate(apply_overrides(copy.deepcopy(cfg), overrides))
            except (ValueError, KeyError) as err:
                self.error(400, str(err))
                return
            save_overrides(overrides)
            log.warning("Einstellungen geändert über Dashboard von %s: %s", self.client_ip(), overrides)
            self.json({"ok": True, "message": "Gespeichert – der Bot übernimmt die Einstellungen innerhalb einer Minute."})

    return Handler


def serve(config_path: str, state_file: str) -> None:
    user = os.getenv("DASHBOARD_USER", "admin")
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if len(password) < 12:
        raise SystemExit("DASHBOARD_PASSWORD fehlt oder ist kürzer als 12 Zeichen (.env).")
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    store = StateStore(state_file)
    last_good = {"cfg": load_config(config_path)}

    def cfg_loader():
        try:
            last_good["cfg"] = load_config(config_path)
        except Exception:
            log.exception("Konfiguration nicht ladbar – zeige letzte gültige")
        return last_good["cfg"]

    secrets_cfg = last_good["cfg"]["secrets"]
    Watchdog(store, Notifier(secrets_cfg["telegram_token"], secrets_cfg["telegram_chat_id"])).start()
    handler = make_handler(cfg_loader, store, user, password, load_secret())
    server = ThreadingHTTPServer((host, port), handler)
    log.info("Dashboard läuft auf http://%s:%d", host, port)
    server.serve_forever()
