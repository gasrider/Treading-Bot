"""Web-Dashboard: Zustand des Bots im Browser ansehen, Not-Aus auslösen, Watchdog.

Läuft als eigener Prozess/Container neben dem Bot und liest nur dessen State-Datei
und Log. Steuerung erfolgt ausschließlich über die Dateien state/STOP und state/RESUME,
die der Bot bei jedem Durchlauf prüft. Geschützt per HTTP-Basic-Auth; im Internet
immer hinter HTTPS (Caddy) betreiben.
"""
import base64
import hmac
import html
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .bot import KILL_SWITCH, RESUME_FILE
from .notifier import Notifier
from .state import StateStore

log = logging.getLogger(__name__)

STALE_SECONDS = 15 * 60          # so lange ohne Lebenszeichen = Bot gilt als ausgefallen
LOG_FILE = Path("logs") / "bot.log"
MAX_FAILED_LOGINS = 10
LOCKOUT_SECONDS = 15 * 60


# ---------------------------------------------------------------- Daten
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
    if state.get("last_error"):
        return "warn", "Läuft, letzter Durchlauf mit Fehler"
    return "ok", "Läuft"


def summary(cfg: dict, state: dict) -> dict:
    trades = state.get("closed_trades", [])
    pnl = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    history = state.get("equity_history", [])
    status, status_text = bot_health(state)
    return {
        "mode": cfg["mode"],
        "exchange": cfg["exchange"],
        "symbols": cfg["symbols"],
        "timeframe": cfg["timeframe"],
        "quote": cfg["quote"],
        "status": status,
        "status_text": status_text,
        "heartbeat": state.get("heartbeat"),
        "last_error": state.get("last_error", ""),
        "halted": bool(state.get("halted")),
        "stop_requested": KILL_SWITCH.exists(),
        "equity": history[-1][1] if history else None,
        "peak_equity": state.get("peak_equity"),
        "cash": state.get("paper_cash"),
        "positions": state.get("positions", {}),
        "trades": len(trades),
        "wins": wins,
        "realized_pnl": round(pnl, 2),
        "equity_history": history,
        "closed_trades": trades[-50:],
    }


def tail(path: Path, lines: int = 80) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return list(deque(f, maxlen=lines))


# ---------------------------------------------------------------- HTML
def equity_svg(history: list, width: int = 800, height: int = 220) -> str:
    if len(history) < 2:
        return '<p class="muted">Der Kapitalverlauf erscheint, sobald der Bot einige Stunden läuft.</p>'
    values = [v for _, v in history]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    pad = 10
    pts = " ".join(
        f"{pad + i * (width - 2 * pad) / (len(values) - 1):.1f},"
        f"{height - pad - (v - lo) / span * (height - 2 * pad):.1f}"
        for i, v in enumerate(values)
    )
    first, last = history[0][0][:10], history[-1][0][:10]
    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="Kapitalverlauf">'
        f'<polyline fill="none" stroke="var(--accent)" stroke-width="2" points="{pts}"/></svg>'
        f'<div class="axis"><span>{first}</span><span>min {lo:.2f} · max {hi:.2f}</span><span>{last}</span></div>'
    )


def fmt(value, digits=2, suffix=""):
    return "–" if value is None else f"{value:,.{digits}f}{suffix}".replace(",", " ")


def render_page(data: dict, log_lines: list[str]) -> str:
    e = html.escape
    q = e(data["quote"])
    positions_rows = "".join(
        f"<tr><td>{e(sym)}</td><td>{p['qty']:.6f}</td><td>{fmt(p['entry'])}</td>"
        f"<td>{fmt(p['stop'])}</td><td>{fmt(p.get('tp'))}</td><td>{e(p['opened_at'][:16].replace('T', ' '))}</td></tr>"
        for sym, p in data["positions"].items()
    ) or '<tr><td colspan="6" class="muted">Keine offenen Positionen</td></tr>'
    trade_rows = "".join(
        f"<tr><td>{e(t['closed_at'][:16].replace('T', ' '))}</td><td>{e(t['symbol'])}</td>"
        f"<td>{fmt(t['entry'])}</td><td>{fmt(t['exit'])}</td>"
        f"<td class=\"{'pos' if t['pnl'] > 0 else 'neg'}\">{t['pnl']:+.2f}</td><td>{e(t['reason'])}</td></tr>"
        for t in reversed(data["closed_trades"])
    ) or '<tr><td colspan="6" class="muted">Noch keine abgeschlossenen Trades</td></tr>'
    win_rate = f"{data['wins'] / data['trades'] * 100:.0f} %" if data["trades"] else "–"
    if data["halted"] or data["stop_requested"]:
        control = (
            '<form method="post" action="/resume" onsubmit="return confirm(\'Handel wirklich wieder freigeben?\')">'
            '<button class="btn resume">▶️ Handel wieder freigeben</button></form>'
        )
    else:
        control = (
            '<form method="post" action="/stop" onsubmit="return confirm(\'NOT-AUS: Alle Positionen sofort '
            'verkaufen und Handel stoppen?\')"><button class="btn stop">🛑 NOT-AUS</button></form>'
        )
    mode_badge = "LIVE – echtes Geld" if data["mode"] == "live" else "PAPER – virtuelles Geld"
    error = f'<p class="err">Letzter Fehler: {e(data["last_error"])}</p>' if data["last_error"] else ""
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Trading-Bot</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --text:#1b1f24; --muted:#6b7280; --line:#e5e7eb;
  --accent:#2563eb; --ok:#16a34a; --warn:#d97706; --bad:#dc2626; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --text:#e6e8eb;
  --muted:#9aa1ab; --line:#2a2f37; --accent:#60a5fa; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--text) }}
main {{ max-width:1000px; margin:0 auto; padding:16px }}
header {{ display:flex; flex-wrap:wrap; gap:12px; align-items:center; justify-content:space-between }}
h1 {{ font-size:1.3rem; margin:0 }} h2 {{ font-size:1rem; margin:0 0 10px }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-top:16px }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px }}
.kpi .label {{ color:var(--muted); font-size:.8rem }} .kpi .value {{ font-size:1.35rem; font-weight:600 }}
.badge {{ display:inline-block; padding:3px 10px; border-radius:999px; font-size:.8rem; font-weight:600; color:#fff }}
.st-ok {{ background:var(--ok) }} .st-warn,.st-unknown {{ background:var(--warn) }} .st-halted,.st-down {{ background:var(--bad) }}
.mode {{ background:{'var(--bad)' if data['mode'] == 'live' else 'var(--accent)'} }}
.table-wrap {{ overflow-x:auto }}
table {{ width:100%; border-collapse:collapse; font-size:.9rem }}
th,td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); white-space:nowrap }}
th {{ color:var(--muted); font-weight:500 }}
.pos {{ color:var(--ok) }} .neg {{ color:var(--bad) }} .muted {{ color:var(--muted) }} .err {{ color:var(--bad); margin:8px 0 0 }}
.chart {{ width:100%; height:auto; display:block }}
.axis {{ display:flex; justify-content:space-between; color:var(--muted); font-size:.75rem }}
.btn {{ border:0; border-radius:8px; padding:10px 18px; font-weight:700; font-size:.95rem; cursor:pointer; color:#fff }}
.stop {{ background:var(--bad) }} .resume {{ background:var(--ok) }}
pre {{ margin:0; font-size:.75rem; overflow-x:auto; white-space:pre-wrap; word-break:break-word; max-height:360px }}
</style></head><body><main>
<header>
  <div><h1>🤖 Trading-Bot</h1>
    <span class="badge st-{data['status']}">{e(data['status_text'])}</span>
    <span class="badge mode">{mode_badge}</span></div>
  {control}
</header>
{error}
<section class="card"><div class="grid">
  <div class="kpi"><div class="label">Kapital</div><div class="value">{fmt(data['equity'])} {q}</div></div>
  <div class="kpi"><div class="label">Höchststand</div><div class="value">{fmt(data['peak_equity'])} {q}</div></div>
  <div class="kpi"><div class="label">Realisierter Gewinn</div>
    <div class="value {'pos' if data['realized_pnl'] >= 0 else 'neg'}">{data['realized_pnl']:+.2f} {q}</div></div>
  <div class="kpi"><div class="label">Trades / Trefferquote</div><div class="value">{data['trades']} / {win_rate}</div></div>
</div>
<p class="muted">{e(data['exchange'])} · {e(', '.join(data['symbols']))} · {e(data['timeframe'])} ·
 letztes Lebenszeichen: {e((data['heartbeat'] or '–').replace('T', ' ')[:19])} UTC</p></section>
<section class="card"><h2>Kapitalverlauf</h2>{equity_svg(data['equity_history'])}</section>
<section class="card"><h2>Offene Positionen</h2><div class="table-wrap"><table>
<tr><th>Paar</th><th>Menge</th><th>Einstieg</th><th>Stop</th><th>Ziel</th><th>Seit (UTC)</th></tr>
{positions_rows}</table></div></section>
<section class="card"><h2>Letzte Trades</h2><div class="table-wrap"><table>
<tr><th>Zeit (UTC)</th><th>Paar</th><th>Kauf</th><th>Verkauf</th><th>Ergebnis {q}</th><th>Grund</th></tr>
{trade_rows}</table></div></section>
<section class="card"><h2>Log (letzte Zeilen)</h2><pre>{e(''.join(log_lines)) or 'Noch keine Einträge'}</pre></section>
<p class="muted">Seite aktualisiert sich jede Minute.</p>
</main></body></html>"""


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


def make_handler(cfg: dict, store: StateStore, user: str, password: str):
    limiter = LoginLimiter()
    expected = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        server_version = "TradingBotDashboard"

        def log_message(self, fmt, *args):
            log.info("%s %s", self.client_ip(), fmt % args)

        def client_ip(self) -> str:
            forwarded = self.headers.get("X-Forwarded-For", "")
            return forwarded.split(",")[0].strip() or self.client_address[0]

        def send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
            )
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def authorized(self) -> bool:
            ip = self.client_ip()
            if limiter.blocked(ip):
                self.send(429, "Zu viele Fehlversuche, bitte 15 Minuten warten.".encode())
                return False
            if hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                return True
            if self.headers.get("Authorization"):
                limiter.fail(ip)
            self.send(401, b"Anmeldung erforderlich", extra={"WWW-Authenticate": 'Basic realm="Trading-Bot"'})
            return False

        def same_origin(self) -> bool:
            origin = self.headers.get("Origin") or self.headers.get("Referer")
            if not origin:
                return True
            return urlparse(origin).netloc == self.headers.get("Host")

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":  # ohne Login, für Docker-Healthcheck
                self.send(200, b"ok", "text/plain")
                return
            if not self.authorized():
                return
            data = summary(cfg, store.load())
            if path == "/":
                self.send(200, render_page(data, tail(LOG_FILE)).encode())
            elif path == "/api/status":
                self.send(200, json.dumps(data, default=str).encode(), "application/json")
            else:
                self.send(404, b"Nicht gefunden", "text/plain")

        do_HEAD = do_GET

        def do_POST(self):
            if not self.authorized():
                return
            if not self.same_origin():
                self.send(403, b"Fremde Herkunft abgelehnt", "text/plain")
                return
            path = urlparse(self.path).path
            KILL_SWITCH.parent.mkdir(parents=True, exist_ok=True)
            if path == "/stop":
                KILL_SWITCH.write_text(datetime.now(timezone.utc).isoformat())
                log.warning("NOT-AUS über Dashboard von %s", self.client_ip())
            elif path == "/resume":
                RESUME_FILE.write_text(datetime.now(timezone.utc).isoformat())
                log.warning("Freigabe über Dashboard von %s", self.client_ip())
            else:
                self.send(404, b"Nicht gefunden", "text/plain")
                return
            self.send(303, b"", extra={"Location": "/"})

    return Handler


def serve(cfg: dict, state_file: str) -> None:
    user = os.getenv("DASHBOARD_USER", "admin")
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if len(password) < 12:
        raise SystemExit("DASHBOARD_PASSWORD fehlt oder ist kürzer als 12 Zeichen (.env).")
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    store = StateStore(state_file)
    secrets = cfg["secrets"]
    Watchdog(store, Notifier(secrets["telegram_token"], secrets["telegram_chat_id"])).start()
    server = ThreadingHTTPServer((host, port), make_handler(cfg, store, user, password))
    log.info("Dashboard läuft auf http://%s:%d", host, port)
    server.serve_forever()
