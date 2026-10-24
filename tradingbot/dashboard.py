"""Web-Dashboard: Zustand und Märkte ansehen, Einstellungen ändern, Not-Aus, Watchdog.

Läuft als eigener Prozess/Container neben dem Bot und liest nur dessen State-Datei
und Log. Steuerung erfolgt ausschließlich über Dateien, die der Bot bei jedem
Durchlauf prüft: state/STOP, state/RESUME und state/settings.yaml (Einstellungen).
Geschützt per HTTP-Basic-Auth; im Internet immer hinter HTTPS (Caddy) betreiben.
"""
import base64
import copy
import hmac
import html
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from . import config as config_module
from .bot import KILL_SWITCH, RESUME_FILE
from .config import EDITABLE, TIMEFRAMES, apply_overrides, load_config, validate
from .notifier import Notifier
from .state import StateStore

log = logging.getLogger(__name__)

STALE_SECONDS = 15 * 60          # so lange ohne Lebenszeichen = Bot gilt als ausgefallen
LOG_FILE = Path("logs") / "bot.log"
MAX_FAILED_LOGINS = 10
LOCKOUT_SECONDS = 15 * 60
MAX_FORM_BYTES = 10_000
COMMON_PAIRS = ["BTC", "ETH", "SOL", "XRP", "ADA", "DOT", "LINK", "LTC", "AVAX", "DOGE"]
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,10}/[A-Z]{3,4}$")
e = html.escape


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
    equity = state.get("equity", history[-1][1] if history else None)
    return {
        "mode": cfg["mode"],
        "exchange": cfg["exchange"],
        "symbols": cfg["symbols"],
        "timeframe": cfg["timeframe"],
        "quote": cfg["quote"],
        "strategy": cfg["strategy"],
        "status": status,
        "status_text": status_text,
        "heartbeat": state.get("heartbeat"),
        "last_error": state.get("last_error", ""),
        "halted": bool(state.get("halted")),
        "stop_requested": KILL_SWITCH.exists(),
        "equity": equity,
        "peak_equity": state.get("peak_equity"),
        "cash": state.get("paper_cash"),
        "positions": state.get("positions", {}),
        "market": state.get("market", {}),
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


# ---------------------------------------------------------------- Formatierung
def fmt(value, digits=2, suffix=""):
    return "–" if value is None else f"{value:,.{digits}f}{suffix}".replace(",", " ")


def fmt_price(value) -> str:
    """Kurse mit sinnvoller Genauigkeit: 71 866.70 / 2 086.82 / 0.152340."""
    if value is None:
        return "–"
    digits = 2 if abs(value) >= 100 else 4 if abs(value) >= 1 else 6
    return fmt(value, digits)


def short_time(iso: str) -> str:
    return iso[5:16].replace("T", " ")


# ---------------------------------------------------------------- Grafiken
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


def market_svg(m: dict, position: dict | None, width: int = 800, height: int = 260) -> str:
    """Kerzen-Chart mit den drei EMAs und – falls offen – Einstieg, Stop und Ziel."""
    candles = m.get("candles") or []
    if len(candles) < 2:
        return '<p class="muted">Noch keine Kursdaten.</p>'
    levels = []
    if position:
        levels = [("Einstieg", position["entry"], "var(--accent)"), ("Stop", position["stop"], "var(--bad)")]
        if position.get("tp"):
            levels.append(("Ziel", position["tp"], "var(--ok)"))
    values = [c[2] for c in candles] + [c[3] for c in candles]
    values += m.get("ema_fast", []) + m.get("ema_slow", []) + m.get("ema_trend", [])
    values += [v for _, v, _ in levels]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    left, right, top, bottom = 4, 74, 8, 8
    plot_w, plot_h = width - left - right, height - top - bottom
    n = len(candles)
    step = plot_w / n

    def y(v):
        return top + (hi - v) / span * plot_h

    def x(i):
        return left + (i + 0.5) * step

    parts = []
    body_w = max(step * 0.6, 1)
    for i, (_, o, h, low, c) in enumerate(candles):
        color = "var(--ok)" if c >= o else "var(--bad)"
        parts.append(f'<line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{y(h):.1f}" y2="{y(low):.1f}" stroke="{color}"/>')
        top_y, bot_y = y(max(o, c)), y(min(o, c))
        parts.append(
            f'<rect x="{x(i) - body_w / 2:.1f}" y="{top_y:.1f}" width="{body_w:.1f}" '
            f'height="{max(bot_y - top_y, 1):.1f}" fill="{color}"/>'
        )
    for key, color in (("ema_trend", "var(--ema-trend)"), ("ema_slow", "var(--ema-slow)"),
                       ("ema_fast", "var(--ema-fast)")):
        series = m.get(key) or []
        if len(series) == n:
            pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(series))
            parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.6" points="{pts}"/>')
    for label, value, color in levels:
        parts.append(
            f'<line x1="{left}" x2="{left + plot_w}" y1="{y(value):.1f}" y2="{y(value):.1f}" '
            f'stroke="{color}" stroke-dasharray="5 4"/>'
            f'<text x="{left + plot_w + 4}" y="{y(value) + 4:.1f}" fill="{color}" class="lbl">{label}</text>'
        )
    for value in (hi, lo):
        if any(abs(y(value) - y(level)) < 14 for _, level, _ in levels):
            continue  # Beschriftung würde eine Linien-Beschriftung überdecken
        parts.append(f'<text x="{left + plot_w + 4}" y="{y(value) + 4:.1f}" class="lbl muted-fill">'
                     f'{e(fmt_price(value))}</text>')
    price = m.get("price")
    if price is not None and lo <= price <= hi:
        parts.append(
            f'<line x1="{left}" x2="{left + plot_w}" y1="{y(price):.1f}" y2="{y(price):.1f}" '
            f'stroke="var(--text)" stroke-width="0.6" stroke-dasharray="2 3"/>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="Kerzen-Chart">'
        + "".join(parts) + "</svg>"
        f'<div class="axis"><span>{e(short_time(candles[0][0]))}</span><span>{e(short_time(candles[-1][0]))} UTC</span></div>'
    )


def market_status(m: dict, position: dict | None, strategy: dict) -> tuple[str, str]:
    """Erklärt in Alltagssprache, was der Bot gerade tut bzw. worauf er wartet."""
    if m.get("error"):
        return "bad", "⚠️ Fehler: " + m["error"]
    if position:
        tp = f", Ziel {fmt_price(position['tp'])}" if position.get("tp") else ""
        return "ok", f"📈 Position offen – Stop {fmt_price(position['stop'])}{tp}"
    if m.get("entry_signal"):
        return "ok", "🟢 Kaufsignal auf der letzten Kerze"
    if not m.get("uptrend"):
        return "muted", (f"⏸️ Kein Aufwärtstrend – Kurs liegt unter der EMA {strategy['ema_trend']}. "
                         "Der Bot kauft nicht.")
    if m.get("fast_above_slow"):
        return "warn", ("⏳ Aufwärtstrend läuft bereits – der Bot kauft nicht hinterher, "
                        "sondern wartet auf das nächste Kreuzungssignal.")
    text = (f"👀 Aufwärtstrend – der Bot wartet, bis die EMA {strategy['ema_fast']} "
            f"die EMA {strategy['ema_slow']} nach oben kreuzt.")
    if not m.get("rsi_ok"):
        text += " (RSI derzeit zu hoch)"
    return "warn", text


def tradingview_url(exchange: str, symbol: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol={exchange.upper()}:{symbol.replace('/', '')}"


def render_market(data: dict) -> str:
    if not data["market"]:
        return ('<section class="card"><h2>Märkte</h2><p class="muted">Die Marktansicht erscheint nach '
                'dem nächsten Durchlauf des Bots (spätestens in einer Minute).</p></section>')
    s = data["strategy"]
    cards = []
    for symbol, m in data["market"].items():
        pos = data["positions"].get(symbol)
        tone, status = market_status(m, pos, s)
        candles = m.get("candles") or []
        change = ""
        if candles:
            pct = (candles[-1][4] / candles[0][1] - 1) * 100
            span_hours = len(candles) * {"15m": 0.25, "30m": 0.5, "1h": 1, "4h": 4, "1d": 24}.get(
                m.get("timeframe", "1h"), 1)
            span = f"{span_hours / 24:g} Tage" if span_hours >= 48 else f"{span_hours:g} Std."
            change = f'<span class="{"pos" if pct >= 0 else "neg"}">{pct:+.2f} %</span> <span class="muted">({span})</span>'
        rsi = m.get("rsi")
        checks = "".join(
            f'<span class="chip {"chip-ok" if ok else "chip-no"}">{"✓" if ok else "✗"} {e(label)}</span>'
            for ok, label in (
                (m.get("uptrend"), f"Kurs über EMA {s['ema_trend']}"),
                (m.get("fast_above_slow"), f"EMA {s['ema_fast']} über EMA {s['ema_slow']}"),
                (m.get("rsi_ok"), f"RSI {fmt(rsi, 1)} < {s['rsi_max_entry']:g}"),
            )
        )
        cards.append(f"""
<div class="market">
  <div class="market-head">
    <div><strong class="sym">{e(symbol)}</strong> <span class="price">{e(fmt_price(m.get('price')))} {e(data['quote'])}</span> {change}</div>
    <a href="{e(tradingview_url(data['exchange'], symbol))}" target="_blank" rel="noopener noreferrer">Live-Chart auf TradingView ↗</a>
  </div>
  <p class="status-line tone-{tone}">{e(status)}</p>
  <div class="chips">{checks}</div>
  {market_svg(m, pos)}
  <div class="legend"><span class="lg lg-fast"></span>EMA {s['ema_fast']} <span class="lg lg-slow"></span>EMA {s['ema_slow']}
   <span class="lg lg-trend"></span>EMA {s['ema_trend']} <span class="muted">· {e(m.get('timeframe', ''))}-Kerzen ·
   Stand {e(short_time(m.get('updated', '')))} UTC</span></div>
</div>""")
    return f'<section class="card"><h2>Märkte – was der Bot gerade sieht</h2>{"".join(cards)}</section>'


# ---------------------------------------------------------------- Seiten
CSS = """
:root { --bg:#f6f7f9; --card:#fff; --text:#1b1f24; --muted:#6b7280; --line:#e5e7eb;
  --accent:#2563eb; --ok:#16a34a; --warn:#d97706; --bad:#dc2626;
  --ema-fast:#f59e0b; --ema-slow:#8b5cf6; --ema-trend:#64748b; }
@media (prefers-color-scheme: dark) { :root { --bg:#0f1115; --card:#181b21; --text:#e6e8eb;
  --muted:#9aa1ab; --line:#2a2f37; --accent:#60a5fa; --ema-trend:#94a3b8; } }
* { box-sizing:border-box }
body { margin:0; font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--text) }
main { max-width:1000px; margin:0 auto; padding:16px }
header { display:flex; flex-wrap:wrap; gap:12px; align-items:center; justify-content:space-between }
h1 { font-size:1.3rem; margin:0 } h2 { font-size:1rem; margin:0 0 10px } h3 { font-size:.95rem; margin:18px 0 8px }
a { color:var(--accent) }
nav { display:flex; gap:6px; margin-top:12px }
nav a { padding:6px 14px; border-radius:8px; text-decoration:none; color:var(--text); border:1px solid var(--line); background:var(--card) }
nav a.active { background:var(--accent); color:#fff; border-color:var(--accent) }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-top:16px }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px }
.kpi .label { color:var(--muted); font-size:.8rem } .kpi .value { font-size:1.35rem; font-weight:600 }
.badge { display:inline-block; padding:3px 10px; border-radius:999px; font-size:.8rem; font-weight:600; color:#fff }
.st-ok { background:var(--ok) } .st-warn,.st-unknown { background:var(--warn) } .st-halted,.st-down { background:var(--bad) }
.mode-paper { background:var(--accent) } .mode-live { background:var(--bad) }
.table-wrap { overflow-x:auto }
table { width:100%; border-collapse:collapse; font-size:.9rem }
th,td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); white-space:nowrap }
th { color:var(--muted); font-weight:500 }
.pos { color:var(--ok) } .neg { color:var(--bad) } .muted { color:var(--muted) } .err { color:var(--bad); margin:8px 0 0 }
.chart { width:100%; height:auto; display:block }
.chart .lbl { font-size:11px } .chart .muted-fill { fill:var(--muted) }
.axis { display:flex; justify-content:space-between; color:var(--muted); font-size:.75rem }
.btn { border:0; border-radius:8px; padding:10px 18px; font-weight:700; font-size:.95rem; cursor:pointer; color:#fff }
.stop { background:var(--bad) } .resume,.save { background:var(--ok) } .reset { background:var(--muted) }
pre { margin:0; font-size:.75rem; overflow-x:auto; white-space:pre-wrap; word-break:break-word; max-height:360px }
.market { padding:14px 0; border-top:1px solid var(--line) } .market:first-of-type { border-top:0; padding-top:0 }
.market-head { display:flex; flex-wrap:wrap; gap:8px; justify-content:space-between; align-items:baseline }
.sym { font-size:1.1rem } .price { font-size:1.1rem; font-weight:600; margin:0 6px }
.status-line { margin:8px 0 } .tone-ok { color:var(--ok) } .tone-warn { color:var(--warn) } .tone-bad { color:var(--bad) } .tone-muted { color:var(--muted) }
.chips { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px }
.chip { font-size:.8rem; padding:2px 8px; border-radius:999px; border:1px solid var(--line) }
.chip-ok { color:var(--ok) } .chip-no { color:var(--muted) }
.legend { font-size:.8rem; margin-top:4px }
.lg { display:inline-block; width:14px; height:3px; vertical-align:middle; margin:0 4px 0 8px }
.lg-fast { background:var(--ema-fast) } .lg-slow { background:var(--ema-slow) } .lg-trend { background:var(--ema-trend) }
.form-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:12px }
label.field { display:flex; flex-direction:column; gap:4px; font-size:.9rem }
label.field small { color:var(--muted) }
input[type=number], input[type=text], select { font:inherit; padding:7px 9px; border-radius:7px;
  border:1px solid var(--line); background:var(--bg); color:var(--text); width:100% }
.pairs { display:flex; flex-wrap:wrap; gap:8px 16px }
.pairs label { display:flex; align-items:center; gap:6px }
.notice { padding:10px 12px; border-radius:8px; margin-top:16px }
.notice-ok { background:color-mix(in srgb, var(--ok) 15%, transparent) }
.notice-bad { background:color-mix(in srgb, var(--bad) 15%, transparent) }
.actions { display:flex; flex-wrap:wrap; gap:10px; margin-top:16px }
"""


def layout(data: dict, active: str, body: str) -> str:
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
    nav = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
        for key, href, label in (("overview", "/", "Übersicht"), ("settings", "/settings", "Einstellungen"))
    )
    refresh = '<meta http-equiv="refresh" content="60">' if active == "overview" else ""
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{refresh}
<title>Trading-Bot</title><style>{CSS}</style></head><body><main>
<header>
  <div><h1>🤖 Trading-Bot</h1>
    <span class="badge st-{data['status']}">{e(data['status_text'])}</span>
    <span class="badge mode-{e(data['mode'])}">{mode_badge}</span></div>
  {control}
</header>
<nav>{nav}</nav>
{body}
</main></body></html>"""


def render_page(data: dict, log_lines: list[str]) -> str:
    q = e(data["quote"])
    positions_rows = "".join(
        f"<tr><td>{e(sym)}</td><td>{p['qty']:.6f}</td><td>{fmt_price(p['entry'])}</td>"
        f"<td>{fmt_price(p['stop'])}</td><td>{fmt_price(p.get('tp'))}</td>"
        f"<td>{e(p['opened_at'][:16].replace('T', ' '))}</td></tr>"
        for sym, p in data["positions"].items()
    ) or '<tr><td colspan="6" class="muted">Keine offenen Positionen</td></tr>'
    trade_rows = "".join(
        f"<tr><td>{e(t['closed_at'][:16].replace('T', ' '))}</td><td>{e(t['symbol'])}</td>"
        f"<td>{fmt_price(t['entry'])}</td><td>{fmt_price(t['exit'])}</td>"
        f"<td class=\"{'pos' if t['pnl'] > 0 else 'neg'}\">{t['pnl']:+.2f}</td><td>{e(t['reason'])}</td></tr>"
        for t in reversed(data["closed_trades"])
    ) or '<tr><td colspan="6" class="muted">Noch keine abgeschlossenen Trades</td></tr>'
    win_rate = f"{data['wins'] / data['trades'] * 100:.0f} %" if data["trades"] else "–"
    error = f'<p class="err">Letzter Fehler: {e(data["last_error"])}</p>' if data["last_error"] else ""
    body = f"""{error}
<section class="card"><div class="grid">
  <div class="kpi"><div class="label">Kapital</div><div class="value">{fmt(data['equity'])} {q}</div></div>
  <div class="kpi"><div class="label">Höchststand</div><div class="value">{fmt(data['peak_equity'])} {q}</div></div>
  <div class="kpi"><div class="label">Realisierter Gewinn</div>
    <div class="value {'pos' if data['realized_pnl'] >= 0 else 'neg'}">{data['realized_pnl']:+.2f} {q}</div></div>
  <div class="kpi"><div class="label">Trades / Trefferquote</div><div class="value">{data['trades']} / {win_rate}</div></div>
</div>
<p class="muted">{e(data['exchange'])} · {e(', '.join(data['symbols']))} · {e(data['timeframe'])} ·
 letztes Lebenszeichen: {e((data['heartbeat'] or '–').replace('T', ' ')[:19])} UTC</p></section>
{render_market(data)}
<section class="card"><h2>Offene Positionen</h2><div class="table-wrap"><table>
<tr><th>Paar</th><th>Menge</th><th>Einstieg</th><th>Stop</th><th>Ziel</th><th>Seit (UTC)</th></tr>
{positions_rows}</table></div></section>
<section class="card"><h2>Letzte Trades</h2><div class="table-wrap"><table>
<tr><th>Zeit (UTC)</th><th>Paar</th><th>Kauf</th><th>Verkauf</th><th>Ergebnis {q}</th><th>Grund</th></tr>
{trade_rows}</table></div></section>
<section class="card"><h2>Kapitalverlauf</h2>{equity_svg(data['equity_history'])}</section>
<section class="card"><h2>Log (letzte Zeilen, Server-Zeit)</h2><pre>{e(''.join(log_lines)) or 'Noch keine Einträge'}</pre></section>
<p class="muted">Seite aktualisiert sich jede Minute.</p>"""
    return layout(data, "overview", body)


def render_settings(data: dict, cfg: dict, message: str = "", ok: bool = True,
                    form: dict | None = None) -> str:
    """Einstellungsformular. `form` enthält bei Fehlern die zuletzt eingegebenen Werte."""
    form = form or {}
    current_pairs = form.get("symbols", cfg["symbols"])
    known = [f"{base}/{cfg['quote']}" for base in COMMON_PAIRS]
    pair_boxes = "".join(
        f'<label><input type="checkbox" name="symbols" value="{e(p)}" {"checked" if p in current_pairs else ""}> {e(p)}</label>'
        for p in dict.fromkeys(known + list(current_pairs))
    )
    tf_value = form.get("timeframe", cfg["timeframe"])
    tf_options = "".join(
        f'<option value="{tf}" {"selected" if tf == tf_value else ""}>{tf}</option>' for tf in TIMEFRAMES
    )

    def field(section, key, label, typ, lo, hi, pct):
        scale = 100 if pct else 1
        name = f"{section}.{key}"
        value_txt = form[name] if name in form else f"{cfg[section][key] * scale:g}"
        lo_txt, hi_txt = f"{lo * scale:g}", f"{hi * scale:g}"
        unit = " %" if pct else ""
        step = "1" if typ is int else "0.1"
        return (f'<label class="field">{e(label)}{unit}'
                f'<input type="number" name="{section}.{key}" value="{e(value_txt)}" min="{lo_txt}" max="{hi_txt}" step="{step}" required>'
                f'<small>{lo_txt} bis {hi_txt}{unit}</small></label>')

    strategy_fields = "".join(field(*f) for f in EDITABLE if f[0] == "strategy")
    risk_fields = "".join(field(*f) for f in EDITABLE if f[0] == "risk")
    notice = ""
    if message:
        notice = f'<p class="notice {"notice-ok" if ok else "notice-bad"}">{e(message)}</p>'
    override_note = (
        '<p class="muted">Aktuell gelten <strong>im Dashboard gespeicherte</strong> Einstellungen.</p>'
        if config_module.SETTINGS_OVERRIDE.exists() else
        '<p class="muted">Aktuell gelten die Einstellungen aus <code>config.yaml</code>.</p>'
    )
    body = f"""{notice}
<section class="card">
<h2>Einstellungen</h2>
<p>Änderungen übernimmt der Bot <strong>innerhalb einer Minute ohne Neustart</strong>. Offene Positionen behalten ihren Stop.
 Nach Strategie-Änderungen am besten neu backtesten (<code>tradingbot backtest</code>).</p>
<p class="muted">Modus: <strong>{e(cfg['mode'])}</strong> · Börse: <strong>{e(cfg['exchange'])}</strong> – aus
 Sicherheitsgründen nur direkt am Server änderbar (<code>tradingbot config</code>).</p>
{override_note}
<form method="post" action="/settings">
<h3>Handelspaare</h3>
<div class="pairs">{pair_boxes}</div>
<div class="form-grid" style="margin-top:10px">
<label class="field">Weitere Paare (durch Komma getrennt)<input type="text" name="extra_symbols" value="{e(form.get('extra_symbols', ''))}" placeholder="z. B. ATOM/{e(cfg['quote'])}"><small>Muss es auf {e(cfg['exchange'])} geben</small></label>
<label class="field">Kerzen-Intervall<select name="timeframe">{tf_options}</select><small>1h = Standard · 4h = weniger, ruhigere Trades</small></label>
</div>
<h3>Strategie</h3>
<div class="form-grid">{strategy_fields}</div>
<h3>Risiko</h3>
<div class="form-grid">{risk_fields}</div>
<div class="actions"><button class="btn save">💾 Speichern</button></div>
</form>
<form method="post" action="/settings/reset" onsubmit="return confirm('Alle im Dashboard gespeicherten Einstellungen verwerfen und config.yaml verwenden?')">
<div class="actions"><button class="btn reset">↩️ Auf config.yaml zurücksetzen</button></div>
</form>
</section>"""
    return layout(data, "settings", body)


def parse_settings(fields: dict[str, list[str]], cfg: dict) -> tuple[dict, dict]:
    """Formular → (Overrides, Formularwerte). Wirft ValueError bei ungültiger Eingabe."""
    form = {"symbols": fields.get("symbols", []), "extra_symbols": fields.get("extra_symbols", [""])[0],
            "timeframe": fields.get("timeframe", [cfg["timeframe"]])[0]}
    symbols = list(form["symbols"])
    for raw in form["extra_symbols"].split(","):
        sym = raw.strip().upper()
        if sym:
            if not SYMBOL_RE.match(sym):
                raise ValueError(f"Ungültiges Paar „{raw.strip()}“ – Format z. B. ATOM/EUR")
            symbols.append(sym)
    for sym in symbols:
        if not SYMBOL_RE.match(sym):
            raise ValueError(f"Ungültiges Paar „{sym}“")
    overrides = {"symbols": list(dict.fromkeys(symbols)), "timeframe": form["timeframe"],
                 "strategy": {}, "risk": {}}
    for section, key, label, typ, lo, hi, pct in EDITABLE:
        name = f"{section}.{key}"
        raw = fields.get(name, [""])[0].strip().replace(",", ".")
        form[name] = raw  # so wie eingegeben (Prozentfelder in %), für erneute Anzeige
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"{label}: bitte eine Zahl eingeben") from None
        if pct:
            value /= 100
        overrides[section][key] = int(round(value)) if typ is int else round(value, 6)
    return overrides, form


def save_overrides(overrides: dict) -> None:
    path = config_module.SETTINGS_OVERRIDE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("# Im Dashboard gespeichert – überschreibt config.yaml. Löschen = zurücksetzen.\n"
                   + yaml.safe_dump(overrides, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


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


def make_handler(cfg_loader, store: StateStore, user: str, password: str):
    """cfg_loader: Funktion, die die aktuelle Konfiguration liefert (inkl. Dashboard-Einstellungen)."""
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
            self.send_header("Referrer-Policy", "same-origin")  # Formulare senden so ihre echte Herkunft
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "form-action 'self'; frame-ancestors 'none'",
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

        def redirect(self, location: str):
            self.send(303, b"", extra={"Location": location})

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/health":  # ohne Login, für Docker-Healthcheck
                self.send(200, b"ok", "text/plain")
                return
            if not self.authorized():
                return
            cfg = cfg_loader()
            data = summary(cfg, store.load())
            if url.path == "/":
                self.send(200, render_page(data, tail(LOG_FILE)).encode())
            elif url.path == "/settings":
                query = parse_qs(url.query)
                message = ""
                if "saved" in query:
                    message = "✅ Gespeichert – der Bot übernimmt die Einstellungen innerhalb einer Minute."
                elif "reset" in query:
                    message = "✅ Zurückgesetzt – es gelten wieder die Werte aus config.yaml."
                self.send(200, render_settings(data, cfg, message).encode())
            elif url.path == "/api/status":
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
                self.redirect("/")
            elif path == "/resume":
                RESUME_FILE.write_text(datetime.now(timezone.utc).isoformat())
                log.warning("Freigabe über Dashboard von %s", self.client_ip())
                self.redirect("/")
            elif path == "/settings":
                self.save_settings()
            elif path == "/settings/reset":
                config_module.SETTINGS_OVERRIDE.unlink(missing_ok=True)
                log.warning("Einstellungen zurückgesetzt über Dashboard von %s", self.client_ip())
                self.redirect("/settings?reset=1")
            else:
                self.send(404, b"Nicht gefunden", "text/plain")

        def save_settings(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_FORM_BYTES:
                self.send(413, b"Formular zu gross", "text/plain")
                return
            fields = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
            cfg = cfg_loader()
            form = None
            try:
                overrides, form = parse_settings(fields, cfg)
                candidate = apply_overrides(copy.deepcopy(cfg), overrides)
                validate(candidate)
            except ValueError as err:
                data = summary(cfg, store.load())
                page = render_settings(data, cfg, f"❌ Nicht gespeichert: {err}", ok=False, form=form)
                self.send(400, page.encode())
                return
            save_overrides(overrides)
            log.warning("Einstellungen geändert über Dashboard von %s: %s", self.client_ip(), overrides)
            self.redirect("/settings?saved=1")

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

    secrets = last_good["cfg"]["secrets"]
    Watchdog(store, Notifier(secrets["telegram_token"], secrets["telegram_chat_id"])).start()
    server = ThreadingHTTPServer((host, port), make_handler(cfg_loader, store, user, password))
    log.info("Dashboard läuft auf http://%s:%d", host, port)
    server.serve_forever()
