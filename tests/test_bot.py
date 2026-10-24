import pandas as pd
import pytest

from tradingbot import bot as bot_module
from tradingbot.bot import TradingBot
from tradingbot.broker import PaperBroker
from tradingbot.data import synthetic
from tradingbot.state import StateStore
from tradingbot.strategy import add_indicators


class FakeExchange:
    """Simulierte Börse: liefert Kerzen bis zu einem einstellbaren Zeitpunkt."""

    def __init__(self, df):
        self.df = df
        self.cursor = 400
        self.price_override = None

    def parse_timeframe(self, tf):
        return 3600

    def fetch_ohlcv(self, symbol, timeframe, limit=500, since=None):
        part = self.df.iloc[max(0, self.cursor - limit):self.cursor]
        # letzte Zeile simuliert die noch offene Kerze in der Zukunft
        future = pd.Timestamp.now(tz="UTC") + pd.Timedelta(minutes=30)
        rows = [[int(ts.timestamp() * 1000), *r] for ts, r in zip(part.index, part.values.tolist())]
        rows.append([int(future.timestamp() * 1000), *part.values[-1].tolist()])
        return rows

    def fetch_ticker(self, symbol):
        price = self.price_override or float(self.df["close"].iat[self.cursor - 1])
        return {"last": price}


class SilentNotifier:
    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)


@pytest.fixture
def setup(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "KILL_SWITCH", tmp_path / "STOP")
    monkeypatch.setattr(bot_module, "RESUME_FILE", tmp_path / "RESUME")
    cfg["symbols"] = ["BTC/EUR"]
    df = synthetic(3000)
    # Index auf Vergangenheit verschieben, damit alle Kerzen "abgeschlossen" sind
    df.index = df.index - (df.index[-1] - pd.Timestamp.now(tz="UTC")) - pd.Timedelta(days=1)
    ex = FakeExchange(df)
    store = StateStore(tmp_path / "state.json")
    state = {}
    broker = PaperBroker(ex, state, 1000, 0.0026, 0.0005)
    notifier = SilentNotifier()
    bot = TradingBot(cfg, ex, broker, store, notifier, state)
    entries = add_indicators(df, cfg["strategy"])["entry"]
    first_entry = next(i for i in range(400, len(df)) if entries.iat[i])
    return bot, ex, notifier, store, first_entry, tmp_path


def test_bot_buys_on_signal_and_stops_out(setup):
    bot, ex, notifier, store, first_entry, _ = setup
    ex.cursor = first_entry + 1  # Signal-Kerze ist gerade abgeschlossen
    bot.step()
    assert "BTC/EUR" in bot.state["positions"]
    pos = bot.state["positions"]["BTC/EUR"]
    assert bot.state["paper_cash"] < 1000
    assert pos["stop"] < pos["entry"]
    assert store.load()["positions"]  # Zustand wurde gespeichert

    ex.price_override = pos["stop"] * 0.99
    bot.step()
    assert not bot.state["positions"]
    assert bot.state["closed_trades"][0]["reason"] == "Stop-Loss"
    loss = -bot.state["closed_trades"][0]["pnl"]
    assert loss < 1000 * 0.015  # ~1 % Risiko + Gebühren
    assert any("KAUF" in m for m in notifier.messages)


def test_same_bar_not_processed_twice(setup):
    bot, ex, _, _, first_entry, _ = setup
    ex.cursor = first_entry + 1
    bot.step()
    qty = bot.state["positions"]["BTC/EUR"]["qty"]
    bot.step()
    assert bot.state["positions"]["BTC/EUR"]["qty"] == qty


def test_kill_switch_closes_everything_and_resume(setup):
    bot, ex, notifier, _, first_entry, tmp_path = setup
    ex.cursor = first_entry + 1
    bot.step()
    (tmp_path / "STOP").write_text("")
    bot.step()                                  # Bot bleibt am Leben, handelt aber nicht
    assert not bot.state["positions"]
    assert bot.state["halted"]
    bot.state["last_bar"].clear()
    bot.step()                                  # auch ein neues Kaufsignal wird ignoriert
    assert not bot.state["positions"]

    (tmp_path / "RESUME").write_text("")
    bot.step()
    assert not bot.state["halted"]
    assert not (tmp_path / "STOP").exists() and not (tmp_path / "RESUME").exists()
    assert any("freigegeben" in m for m in notifier.messages)


def test_equity_history_recorded_hourly(setup):
    bot, ex, _, _, first_entry, _ = setup
    ex.cursor = first_entry + 1
    bot.step()
    bot.step()
    assert len(bot.state["equity_history"]) == 1
    assert bot.state["equity_history"][0][1] == pytest.approx(1000, rel=0.01)


def test_drawdown_halts_bot(setup):
    bot, ex, notifier, _, first_entry, _ = setup
    ex.cursor = first_entry + 1
    bot.step()
    bot.state["peak_equity"] = 5000  # simuliert großen Verlust seit Höchststand
    bot.step()
    assert bot.state["halted"]
    assert not bot.state["positions"]
    assert any("Drawdown" in m for m in notifier.messages)
