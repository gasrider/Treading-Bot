import base64
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest
import yaml

from tradingbot import bot as bot_module
from tradingbot import config as config_module
from tradingbot import dashboard
from tradingbot.state import StateStore

PASSWORD = "sehr-geheimes-passwort"


def iso(minutes_ago=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture
def env(cfg, tmp_path, monkeypatch):
    for module in (bot_module, dashboard):
        monkeypatch.setattr(module, "KILL_SWITCH", tmp_path / "STOP")
        monkeypatch.setattr(module, "RESUME_FILE", tmp_path / "RESUME")
    monkeypatch.setattr(dashboard, "LOG_FILE", tmp_path / "bot.log")
    monkeypatch.setattr(config_module, "SETTINGS_OVERRIDE", tmp_path / "settings.yaml")
    (tmp_path / "bot.log").write_text("Zeile <script>alert(1)</script>\n")
    store = StateStore(tmp_path / "state.json")
    store.save({
        "heartbeat": iso(1), "halted": False, "peak_equity": 1050.0, "paper_cash": 700.0,
        "positions": {"BTC/EUR": {"qty": 0.005, "entry": 60000, "stop": 58000, "tp": 64000,
                                  "opened_at": iso(30)}},
        "closed_trades": [{"symbol": "ETH/EUR", "entry": 2000, "exit": 2100, "qty": 0.1, "pnl": 9.5,
                           "reason": "Take-Profit", "opened_at": iso(300), "closed_at": iso(100)}],
        "equity_history": [[iso(120), 1000.0], [iso(60), 1020.0], [iso(0), 1010.0]],
    })
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.make_handler(lambda: cfg, store, "admin", PASSWORD))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", tmp_path, store
    server.shutdown()


def request(url, method="GET", password=PASSWORD, headers=None):
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    if password:
        token = base64.b64encode(f"admin:{password}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_requires_login(env):
    url, _, _ = env
    assert request(url + "/", password=None)[0] == 401
    assert request(url + "/", password="falsch")[0] == 401
    assert request(url + "/health", password=None)[0] == 200


def test_page_shows_data_and_escapes_log(env):
    url, _, _ = env
    status, body = request(url + "/")
    assert status == 200
    assert "BTC/EUR" in body and "Take-Profit" in body and "<polyline" in body
    assert "<script>alert(1)</script>" not in body
    assert "NOT-AUS" in body


def test_stop_and_resume_buttons_write_control_files(env):
    url, tmp_path, _ = env
    assert request(url + "/stop", "POST")[0] == 303
    assert (tmp_path / "STOP").exists()
    assert request(url + "/resume", "POST")[0] == 303
    assert (tmp_path / "RESUME").exists()


def test_cross_origin_post_rejected(env):
    url, tmp_path, _ = env
    status, _ = request(url + "/stop", "POST", headers={"Origin": "https://boese-seite.example"})
    assert status == 403
    assert not (tmp_path / "STOP").exists()


def test_brute_force_lockout(env):
    url, _, _ = env
    for _ in range(dashboard.MAX_FAILED_LOGINS):
        request(url + "/", password="falsch")
    assert request(url + "/")[0] == 429  # auch das richtige Passwort ist jetzt gesperrt


def test_api_status_json(env):
    url, _, _ = env
    status, body = request(url + "/api/status")
    assert status == 200 and '"status": "ok"' in body and '"equity": 1010.0' in body


def test_health_states():
    assert dashboard.bot_health({})[0] == "unknown"
    assert dashboard.bot_health({"heartbeat": iso(1)})[0] == "ok"
    assert dashboard.bot_health({"heartbeat": iso(30)})[0] == "down"
    assert dashboard.bot_health({"heartbeat": iso(1), "halted": True, "halt_reason": "x"})[0] == "halted"


def test_watchdog_alerts_once_and_recovers(env):
    _, _, store = env

    class Recorder:
        messages = []

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


def post_form(url, body):
    req = urllib.request.Request(url, data=body.encode(), method="POST")
    token = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def settings_body(**changes):
    from urllib.parse import urlencode

    fields = {"symbols": ["BTC/EUR", "SOL/EUR"], "extra_symbols": "atom/eur", "timeframe": "4h",
              "strategy.ema_fast": "20", "strategy.ema_slow": "50", "strategy.ema_trend": "200",
              "strategy.rsi_max_entry": "70", "strategy.stop_atr": "2", "strategy.take_profit_atr": "4",
              "strategy.trailing_atr": "2,5", "risk.risk_per_trade": "0,5", "risk.max_position_pct": "30",
              "risk.max_open_positions": "3", "risk.max_daily_loss": "3", "risk.max_drawdown": "15"}
    fields.update(changes)
    return urlencode(fields, doseq=True)


def test_settings_page_shows_form(env):
    url, _, _ = env
    status, body = request(url + "/settings")
    assert status == 200 and 'name="risk.risk_per_trade" value="1"' in body and "SOL/EUR" in body


def test_settings_save_writes_validated_overrides(env):
    url, tmp_path, _ = env
    status, _ = post_form(url + "/settings", settings_body())
    assert status == 303
    saved = yaml.safe_load((tmp_path / "settings.yaml").read_text())
    assert saved["symbols"] == ["BTC/EUR", "SOL/EUR", "ATOM/EUR"]
    assert saved["timeframe"] == "4h"
    assert saved["risk"]["risk_per_trade"] == pytest.approx(0.005)
    assert saved["strategy"]["trailing_atr"] == pytest.approx(2.5)
    assert saved["risk"]["max_open_positions"] == 3
    assert "mode" not in saved


def test_settings_invalid_rejected_and_values_kept(env):
    url, tmp_path, _ = env
    status, body = post_form(url + "/settings", settings_body(**{"risk.risk_per_trade": "10"}))
    assert status == 400 and "Risiko pro Trade" in body and 'value="10"' in body
    assert not (tmp_path / "settings.yaml").exists()
    status, body = post_form(url + "/settings", settings_body(extra_symbols="<script>"))
    assert status == 400 and "<script>" not in body


def test_settings_reset(env):
    url, tmp_path, _ = env
    (tmp_path / "settings.yaml").write_text("timeframe: 4h\n")
    assert post_form(url + "/settings/reset", "")[0] == 303
    assert not (tmp_path / "settings.yaml").exists()


def test_overview_shows_market(env):
    url, _, store = env
    state = store.load()
    candles = [[iso(60 * (96 - i)), 100 + i, 102 + i, 99 + i, 101 + i] for i in range(96)]
    state["market"] = {"ETH/EUR": {"updated": iso(0), "timeframe": "1h", "price": 196.5, "candles": candles,
                                   "ema_fast": [100.0 + i for i in range(96)], "ema_slow": [99.0 + i for i in range(96)],
                                   "ema_trend": [95.0 + i for i in range(96)], "rsi": 64.2, "atr": 2.0,
                                   "uptrend": True, "fast_above_slow": True, "rsi_ok": True,
                                   "entry_signal": False, "exit_signal": False, "error": ""}}
    store.save(state)
    status, body = request(url + "/")
    assert status == 200
    assert "196.50" in body and "<rect" in body and "kauft nicht hinterher" in body
    assert "tradingview.com/chart/?symbol=KRAKEN:ETHEUR" in body


def test_browser_form_posts_are_accepted(env):
    """Regression: mit Referrer-Policy no-referrer schickten Browser 'Origin: null' -> Not-Aus ging nicht."""
    url, tmp_path, _ = env
    req = urllib.request.Request(url + "/", method="HEAD")
    token = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.headers["Referrer-Policy"] == "same-origin"
        assert "script-src 'unsafe-inline'" in resp.headers["Content-Security-Policy"]  # confirm()-Rückfrage
    host = url.split("//")[1]
    assert request(url + "/stop", "POST", headers={"Origin": f"http://{host}"})[0] == 303
    assert (tmp_path / "STOP").exists()
    assert request(url + "/resume", "POST", headers={"Origin": "null"})[0] == 403
