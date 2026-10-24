import base64
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest

from tradingbot import bot as bot_module
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
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.make_handler(cfg, store, "admin", PASSWORD))
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
