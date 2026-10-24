import base64
import http.client
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pandas as pd
import pytest
import yaml

from tradingbot import config as config_module
from tradingbot import control, dashboard
from tradingbot.data import synthetic
from tradingbot.state import StateStore
from tests.test_bot import FakeExchange, patch_control
from tests.test_multiasset import fake_yahoo_download
from tradingbot.sources import YahooSource

PASSWORD = "sehr-geheimes-passwort"
SECRET = b"s" * 32


def iso(minutes_ago=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture
def env(cfg, tmp_path, monkeypatch):
    patch_control(monkeypatch, tmp_path)
    monkeypatch.setattr(dashboard, "LOG_FILE", tmp_path / "bot.log")
    monkeypatch.setattr(config_module, "SETTINGS_OVERRIDE", tmp_path / "settings.yaml")
    (tmp_path / "bot.log").write_text("Zeile 1\nZeile <script>2</script>\n")
    store = StateStore(tmp_path / "state.json")
    candles = [[iso(60 * (96 - i)), 100 + i, 102 + i, 99 + i, 101 + i] for i in range(96)]
    store.save({
        "heartbeat": iso(1), "halted": False, "peak_equity": 1050.0, "paper_cash": 700.0, "equity": 1010.0,
        "day_start_equity": 1000.0,
        "positions": {"BTC/EUR": {"qty": 0.005, "entry": 60000, "stop": 58000, "tp": 64000, "cost": 300.0,
                                  "opened_at": iso(30)}},
        "closed_trades": [{"symbol": "ETH/EUR", "entry": 2000, "exit": 2100, "qty": 0.1, "pnl": 9.5,
                           "reason": "Take-Profit", "opened_at": iso(300), "closed_at": iso(100)}],
        "equity_history": [[iso(120), 1000.0], [iso(60), 1020.0], [iso(0), 1010.0]],
        "benchmark": {"time": iso(200), "equity": 1000.0, "prices": {"BTC/EUR": 60000, "ETH/EUR": 180}},
        "last_bar": {"BTC/EUR": "2026-01-01 10:00:00+00:00"},
        "events": [{"t": iso(5), "kind": "trade", "text": "🟢 KAUF BTC/EUR"}],
        "market": {
            "BTC/EUR": {"updated": iso(0), "timeframe": "1h", "price": 61000.0, "candles": candles, "rsi": 55.0,
                        "uptrend": True, "fast_above_slow": True, "rsi_ok": True, "entry_signal": False, "error": ""},
            "ETH/EUR": {"updated": iso(0), "timeframe": "1h", "price": 198.0, "candles": candles, "rsi": 40.0,
                        "uptrend": False, "fast_above_slow": False, "rsi_ok": True, "entry_signal": False, "error": ""},
        },
    })
    df = synthetic(800)
    df.index = df.index - (df.index[-1] - pd.Timestamp.now(tz="UTC")) - pd.Timedelta(hours=1)
    fake = FakeExchange(df)
    fake.cursor = 800
    charts = dashboard.ChartProvider(exchange_factory=lambda name: fake,
                                     yahoo=YahooSource(download=fake_yahoo_download))
    handler = dashboard.make_handler(lambda: cfg, store, "admin", PASSWORD, SECRET, charts)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"port": server.server_port, "tmp": tmp_path, "store": store, "fake": fake, "cfg": cfg}
    server.shutdown()


def call(env, path, method="GET", body=None, cookie=None, basic=False, headers=None, write_header=True):
    conn = http.client.HTTPConnection("127.0.0.1", env["port"], timeout=5)
    hdrs = {"Host": f"127.0.0.1:{env['port']}"}
    if method == "POST" and write_header:
        hdrs["X-Requested-With"] = "tradingbot"
    if cookie:
        hdrs["Cookie"] = cookie
    if basic:
        hdrs["Authorization"] = "Basic " + base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    hdrs.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    if data:
        hdrs["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    ctype = resp.getheader("Content-Type", "")
    payload = json.loads(raw) if ctype.startswith("application/json") else raw
    return resp.status, payload, resp


def login(env, password=PASSWORD, remember=True, headers=None):
    status, payload, resp = call(env, "/api/login", "POST",
                                 {"user": "admin", "password": password, "remember": remember}, headers=headers)
    return status, resp.getheader("Set-Cookie", "")


@pytest.fixture
def cookie(env):
    status, set_cookie = login(env)
    assert status == 200
    return set_cookie.split(";")[0]


# ------------------------------------------------------------ Auslieferung & Sicherheit
def test_index_and_static_files_served_with_strict_csp(env):
    status, body, resp = call(env, "/")
    assert status == 200 and b"/static/app.js" in body
    csp = resp.getheader("Content-Security-Policy")
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert resp.getheader("Referrer-Policy") == "same-origin"
    status, body, resp = call(env, "/static/vendor/lightweight-charts.standalone.production.js")
    assert status == 200 and resp.getheader("Content-Type").startswith("text/javascript") and b"Lightweight Charts" in body
    assert call(env, "/static/../dashboard.py")[0] == 404
    assert call(env, "/static/nope.js")[0] == 404
    assert call(env, "/health")[0] == 200


def test_api_requires_login(env):
    assert call(env, "/api/status")[0] == 401
    assert call(env, "/api/session")[1]["authenticated"] is False
    assert call(env, "/api/status", basic=True)[0] == 200  # für Skripte/curl


def test_login_sets_secure_session_cookie(env):
    assert login(env, "falsch")[0] == 401
    status, set_cookie = login(env, headers={"X-Forwarded-Proto": "https"})
    assert status == 200
    assert "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie and "Secure" in set_cookie
    assert f"Max-Age={30 * 86400}" in set_cookie
    cookie = set_cookie.split(";")[0]
    assert call(env, "/api/session", cookie=cookie)[1]["authenticated"] is True
    assert call(env, "/api/status", cookie=cookie)[0] == 200
    assert call(env, "/api/status", cookie=cookie[:-2] + "00")[0] == 401  # manipuliert


def test_short_session_without_remember(env):
    status, set_cookie = login(env, remember=False)
    assert status == 200 and "Max-Age=86400" in set_cookie and "Secure" not in set_cookie


def test_login_lockout(env):
    for _ in range(dashboard.MAX_FAILED_LOGINS):
        login(env, "falsch")
    assert login(env)[0] == 429


def test_sessions_expire_and_depend_on_password():
    s = dashboard.Sessions(SECRET, "admin", PASSWORD)
    token = s.issue()
    assert s.valid(token)
    assert not dashboard.Sessions(SECRET, "admin", "anderes-passwort-123").valid(token)
    expired = f"{int(time.time()) - 5}." + token.split(".")[1]
    assert not s.valid(expired)
    assert not s.valid("kaputt")


def test_writes_need_header_and_same_origin(env, cookie):
    assert call(env, "/api/control", "POST", {"action": "pause"}, cookie=cookie, write_header=False)[0] == 403
    status = call(env, "/api/control", "POST", {"action": "pause"}, cookie=cookie,
                  headers={"Origin": "https://boese-seite.example"})[0]
    assert status == 403 and not control.PAUSE_FILE.exists()
    assert call(env, "/api/control", "POST", {"action": "pause"}, cookie=cookie,
                headers={"Origin": f"http://127.0.0.1:{env['port']}"})[0] == 200


# ------------------------------------------------------------ Daten
def test_status_payload(env, cookie):
    status, d, _ = call(env, "/api/status", cookie=cookie)
    assert status == 200
    assert d["bot"]["status"] == "ok" and d["bot"]["mode"] == "paper" and d["bot"]["next_eval"]
    pos = d["positions"][0]
    assert pos["symbol"] == "BTC/EUR" and pos["price"] == 61000.0
    assert pos["upnl"] == pytest.approx(0.005 * 61000 - 300)
    assert pos["progress"] == pytest.approx((61000 - 58000) / (64000 - 58000))
    a = d["account"]
    assert a["today_pnl"] == 10.0 and a["total_pct"] == 1.0 and a["realized"] == 9.5
    assert a["benchmark_pct"] == pytest.approx(((61000 / 60000 - 1) + (198 / 180 - 1)) / 2 * 100, abs=0.01)
    assert a["risk_per_trade_eur"] == pytest.approx(10.1)
    by_sym = {m["symbol"]: m for m in d["markets"]}
    btc, eth = by_sym["BTC/EUR"], by_sym["ETH/EUR"]
    assert btc["text"].startswith("Position offen")
    assert "Kein Aufwärtstrend" in eth["text"] and len(eth["spark"]) == 96
    assert d["stats"]["count"] == 1 and d["events"][0]["kind"] == "trade"


def test_chart_endpoint(env, cookie):
    status, d, _ = call(env, "/api/chart?symbol=BTC/EUR&tf=1h", cookie=cookie)
    assert status == 200 and d["source"] == "exchange"
    assert len(d["candles"]) > 500 and {"time", "open", "high", "low", "close"} <= set(d["candles"][0])
    assert len(d["ema_trend"]) == len(d["candles"]) and len(d["rsi"]) == len(d["candles"])
    assert d["levels"] == {"entry": 60000, "stop": 58000, "tp": 64000}
    assert d["markers"] and d["markers"][-1]["text"] == "Offen"
    assert call(env, "/api/chart?symbol=<x>&tf=1h", cookie=cookie)[0] == 400
    assert call(env, "/api/chart?symbol=BTC/EUR&tf=7m", cookie=cookie)[0] == 400


def test_chart_falls_back_to_bot_snapshot(env, cookie):
    def broken(*a, **k):
        raise RuntimeError("offline")
    env["fake"].fetch_ohlcv = broken
    status, d, _ = call(env, "/api/chart?symbol=ETH/EUR&tf=4h", cookie=cookie)
    assert status == 200 and d["source"] == "snapshot" and len(d["candles"]) == 96 and d["timeframe"] == "1h"
    assert call(env, "/api/chart?symbol=SOL/EUR&tf=1h", cookie=cookie)[0] == 502


def test_trades_and_csv_export(env, cookie):
    status, d, _ = call(env, "/api/trades", cookie=cookie)
    assert status == 200 and d["trades"][0]["symbol"] == "ETH/EUR" and d["stats"]["total_pnl"] == 9.5
    status, raw, resp = call(env, "/api/trades.csv", cookie=cookie)
    text = raw.decode("utf-8")
    assert status == 200 and "attachment" in resp.getheader("Content-Disposition")
    assert text.startswith("﻿") and "Markt;Klasse;Menge" in text and "ETH/EUR;Krypto;0,1;2000;2100;;9,5;Take-Profit" in text


def test_logs_endpoint(env, cookie):
    d = call(env, "/api/logs?n=1", cookie=cookie)[1]
    assert d["lines"] == ["Zeile <script>2</script>"]  # Escaping passiert im Browser (textContent)
    assert call(env, "/api/logs?n=abc", cookie=cookie)[0] == 200


# ------------------------------------------------------------ Steuerung
def test_control_actions(env, cookie):
    def act(action, **extra):
        return call(env, "/api/control", "POST", {"action": action, **extra}, cookie=cookie)

    assert act("stop")[0] == 200 and control.KILL_SWITCH.exists()
    assert act("resume")[0] == 200 and control.RESUME_FILE.exists()
    assert act("pause")[0] == 200 and control.PAUSE_FILE.exists()
    assert call(env, "/api/status", cookie=cookie)[1]["bot"]["paused"] is True
    assert act("unpause")[0] == 200 and not control.PAUSE_FILE.exists()
    assert act("close", symbol="ETH/EUR")[0] == 400
    assert act("close", symbol="BTC/EUR")[0] == 200
    assert control.take_commands() == [{"action": "close", "symbol": "BTC/EUR"}]
    assert act("bogus")[0] == 400


# ------------------------------------------------------------ Einstellungen
def settings_body(**changes):
    body = {"symbols": ["BTC/EUR", "sol/eur"], "timeframe": "4h",
            "strategy": {"ema_fast": 20, "ema_slow": 50, "ema_trend": 200, "rsi_max_entry": 70,
                         "stop_atr": 2, "take_profit_atr": 4, "trailing_atr": "2,5"},
            "risk": {"risk_per_trade": 0.005, "max_position_pct": 0.3, "max_open_positions": 3,
                     "max_daily_loss": 0.03, "max_drawdown": 0.15},
            "scanner": {"top_pct": 0.5}}
    for path, value in changes.items():
        sec, key = path.split(".")
        body[sec][key] = value
    return body


def test_settings_get(env, cookie):
    d = call(env, "/api/settings", cookie=cookie)[1]
    keys = {f["key"] for f in d["fields"]}
    assert {"risk_per_trade", "ema_trend"} <= keys and "mode" not in keys
    assert d["mode"] == "paper" and "SOL/EUR" in d["pairs"] and d["equity"] == 1010.0
    assert all(f["help"] for f in d["fields"])


def test_settings_save_validated(env, cookie):
    status, d, _ = call(env, "/api/settings", "POST", settings_body(), cookie=cookie)
    assert status == 200, d
    saved = yaml.safe_load(config_module.SETTINGS_OVERRIDE.read_text())
    assert saved["symbols"] == ["BTC/EUR", "SOL/EUR"] and saved["timeframe"] == "4h"
    assert saved["strategy"]["trailing_atr"] == 2.5 and saved["risk"]["max_open_positions"] == 3
    assert "mode" not in saved


@pytest.mark.parametrize("change, message", [
    ({"risk.risk_per_trade": 0.1}, "Risiko pro Trade"),
    ({"strategy.ema_fast": "abc"}, "Zahl"),
    ({"strategy.ema_fast": "nan"}, "Zahl"),
    ({"strategy.ema_fast": 80}, "schnelle EMA"),
])
def test_settings_rejects_invalid(env, cookie, change, message):
    status, d, _ = call(env, "/api/settings", "POST", settings_body(**change), cookie=cookie)
    assert status == 400 and message in d["error"]
    assert not config_module.SETTINGS_OVERRIDE.exists()


def test_settings_rejects_bad_symbol_and_mode_injection(env, cookie):
    body = settings_body()
    body["symbols"] = ["<script>"]
    assert call(env, "/api/settings", "POST", body, cookie=cookie)[0] == 400
    body = settings_body()
    body["mode"] = "live"
    assert call(env, "/api/settings", "POST", body, cookie=cookie)[0] == 200
    assert "mode" not in yaml.safe_load(config_module.SETTINGS_OVERRIDE.read_text())


def test_settings_reset(env, cookie):
    config_module.SETTINGS_OVERRIDE.write_text("timeframe: 4h\n")
    assert call(env, "/api/settings/reset", "POST", {}, cookie=cookie)[0] == 200
    assert not config_module.SETTINGS_OVERRIDE.exists()


# ------------------------------------------------------------ Gesundheit & Watchdog
def test_health_states():
    assert dashboard.bot_health({})[0] == "unknown"
    assert dashboard.bot_health({"heartbeat": iso(1)})[0] == "ok"
    assert dashboard.bot_health({"heartbeat": iso(1), "paused": True})[0] == "paused"
    assert dashboard.bot_health({"heartbeat": iso(30)})[0] == "down"
    assert dashboard.bot_health({"heartbeat": iso(1), "halted": True, "halt_reason": "x"})[0] == "halted"


def test_watchdog_alerts_once_and_recovers(env):
    store = env["store"]

    class Recorder:
        def __init__(self):
            self.messages = []

        def send(self, text):
            self.messages.append(text)

    rec = Recorder()
    dog = dashboard.Watchdog(store, rec)
    dog.check()
    assert rec.messages == []
    state = store.load()
    state["heartbeat"] = iso(20)
    store.save(state)
    dog.check()
    dog.check()
    assert len(rec.messages) == 1 and "Lebenszeichen" in rec.messages[0]
    state["heartbeat"] = iso(0)
    store.save(state)
    dog.check()
    assert "wieder" in rec.messages[-1]


# ------------------------------------------------------------ Mehrere Anlageklassen & KI
def test_status_contains_all_markets_regime_and_ai(env, cookie):
    state = env["store"].load()
    state["regime"] = {"state": "caution", "label": "Vorsicht", "text": "VIX 24", "multiplier": 0.5}
    state["ai"] = {"status": {"available": True}, "decisions": [
        {"t": iso(1), "symbol": "AAPL", "decision": "reject", "reason": "Zahlen morgen", "enforced": True,
         "approve": False, "confidence": "hoch", "risks": [], "error": "", "cost_usd": 0.02}],
        "shadow": [{"t": iso(1), "symbol": "AAPL", "entry": 100, "stop": 95, "tp": 110, "status": "wäre Verlust",
                    "result_pct": -5.0, "reason": "x", "enforced": True}]}
    env["store"].save(state)
    d = call(env, "/api/status", cookie=cookie)[1]
    classes = {m["asset_class"] for m in d["markets"]}
    assert {"crypto", "stocks", "etfs", "commodities", "forex"} <= classes
    assert d["regime"]["label"] == "Vorsicht"
    assert d["ai"]["mode"] == "veto" and d["ai"]["available"] and d["ai"]["bilanz"]["would_lose"] == 1
    assert d["ai"]["decisions"][0]["symbol"] == "AAPL" and d["asset_classes"]["commodities"] == "Rohstoffe"


def test_chart_for_stock_uses_yahoo(env, cookie):
    status, d, _ = call(env, "/api/chart?symbol=AAPL&tf=1h", cookie=cookie)
    assert status == 200 and d["source"] == "yahoo" and len(d["candles"]) > 300
    status, d, _ = call(env, "/api/chart?symbol=GC%3DF&tf=4h", cookie=cookie)
    assert status == 200 and len(d["candles"]) > 50


def test_settings_save_universe_and_ai_mode(env, cookie):
    body = settings_body()
    body["universe"] = {"stocks": ["aapl", "OMV.VI"], "commodities": ["GC=F"], "etfs": [], "forex": []}
    body["ai"] = {"mode": "advisory"}
    body["scanner"] = {"top_pct": 0.3}
    status, d, _ = call(env, "/api/settings", "POST", body, cookie=cookie)
    assert status == 200, d
    saved = yaml.safe_load(config_module.SETTINGS_OVERRIDE.read_text())
    assert saved["universe"]["stocks"] == ["AAPL", "OMV.VI"] and saved["ai"] == {"mode": "advisory"}
    assert saved["scanner"]["top_pct"] == 0.3


@pytest.mark.parametrize("universe, ai_mode, msg", [
    ({"stocks": ["VOD.L"]}, None, "Ticker"),
    ({"stocks": ["<b>"]}, None, "Ticker"),
    (None, "allmächtig", "KI-Modus"),
])
def test_settings_rejects_bad_universe_or_ai(env, cookie, universe, ai_mode, msg):
    body = settings_body()
    if universe:
        body["universe"] = universe
    if ai_mode:
        body["ai"] = {"mode": ai_mode}
    status, d, _ = call(env, "/api/settings", "POST", body, cookie=cookie)
    assert status == 400 and msg in d["error"]
