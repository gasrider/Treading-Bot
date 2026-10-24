"""Tests für Märkte, Datenquellen, Scanner, Marktlage und den Handel über alle Anlageklassen."""
import numpy as np
import pandas as pd
import pytest

from tradingbot.broker import PaperBroker
from tradingbot.markets import build_universe, valid_yahoo_ticker, yahoo_currency
from tradingbot.scanner import market_regime, momentum_score, rank_percentiles
from tradingbot.sources import MarketData, YahooSource, drop_forming, extract, resample
from tradingbot.state import StateStore
from tradingbot.strategy import add_indicators

# ------------------------------------------------------------ Hilfen: künstliche Yahoo-Daten
YAHOO_STATE = {"closed": False, "fail": False, "calls": []}


def series(seed: int, bars: int, freq: str, start: float, drift: float = 0.0008, end=None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = drift + rng.normal(0, 0.006, bars)
    close = start * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[start], close[:-1]])
    high = np.maximum(open_, close) * 1.002
    low = np.minimum(open_, close) * 0.998
    end = end or pd.Timestamp.now(tz="UTC").floor(freq) - pd.Timedelta(freq)
    idx = pd.date_range(end=end, periods=bars, freq=freq, tz="UTC")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1.0}, index=idx)


PRICES = {"AAPL": 200.0, "SAP.DE": 220.0, "GC=F": 4000.0, "EURUSD=X": 1.10, "EURCHF=X": 0.95, "^GSPC": 6000.0, "^VIX": 15.0}


def fake_yahoo_download(tickers, period, interval):
    YAHOO_STATE["calls"].append((tuple(tickers), interval))
    if YAHOO_STATE["fail"]:
        raise RuntimeError("Too Many Requests")
    frames = {}
    for i, t in enumerate(tickers):
        base = PRICES.get(t, 100.0)
        if interval == "1d":
            df = series(i + 1, 400, "1D", base, drift=0.001 if t != "^VIX" else 0.0)
            if t == "^VIX":
                df[:] = PRICES["^VIX"]
        else:
            df = series(i + 7, 600, "1h", base, drift=0.0 if t.endswith("=X") else 0.0008)
            if t.endswith("=X"):
                df[["Open", "High", "Low", "Close"]] = base
        if interval == "5m":
            last = df.iloc[[-1]].copy()
            age = pd.Timedelta(days=2) if YAHOO_STATE["closed"] else pd.Timedelta(minutes=5)
            last.index = pd.DatetimeIndex([pd.Timestamp.now(tz="UTC") - age])
            df = last
        frames[t] = df
    return pd.concat(frames, axis=1)  # MultiIndex (Ticker, Feld) wie yfinance


@pytest.fixture(autouse=True)
def reset_yahoo():
    YAHOO_STATE.update(closed=False, fail=False, calls=[])


# ------------------------------------------------------------ Märkte
def test_currency_and_ticker_rules():
    assert yahoo_currency("AAPL") == "USD" and yahoo_currency("SAP.DE") == "EUR"
    assert yahoo_currency("OMV.VI") == "EUR" and yahoo_currency("NESN.SW") == "CHF"
    assert yahoo_currency("EURUSD=X") == "USD" and yahoo_currency("GC=F") == "USD"
    assert valid_yahoo_ticker("BRK-B") and valid_yahoo_ticker("^GSPC")
    assert not valid_yahoo_ticker("VOD.L")       # Pence nicht unterstützt
    assert not valid_yahoo_ticker("<script>") and not valid_yahoo_ticker("aapl ")


def test_build_universe(cfg):
    cfg["universe"] = {"stocks": ["AAPL"], "forex": ["EURUSD=X"]}
    u = build_universe(cfg)
    assert u["BTC/EUR"].source == "exchange" and u["AAPL"].source == "yahoo"
    assert u["AAPL"].currency == "USD" and u["EURUSD=X"].asset_class == "forex"
    assert "AAPL" not in build_universe(cfg, include_yahoo=False)


# ------------------------------------------------------------ Datenquellen
def test_extract_resample_and_forming():
    raw = fake_yahoo_download(["AAPL", "SAP.DE"], "180d", "1h")
    df = extract(raw, "AAPL")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"] and str(df.index.tz) == "UTC"
    assert extract(raw, "MSFT").empty
    four = resample(df, "4h")
    assert len(four) < len(df) and four["high"].iat[1] >= four["close"].iat[1]
    forming = df.copy()
    forming.loc[pd.Timestamp.now(tz="UTC").floor("h")] = forming.iloc[-1].values
    assert len(drop_forming(forming, "1h")) == len(forming) - 1


def test_yahoo_batching_cache_and_backoff():
    y = YahooSource(download=fake_yahoo_download)
    y.refresh(["AAPL", "SAP.DE"], "1h")
    y.refresh(["AAPL", "SAP.DE"], "1h")
    assert len(YAHOO_STATE["calls"]) == 1          # eine gebündelte Abfrage, danach Cache
    assert len(y.closed_candles("AAPL", "1h")) == 600
    YAHOO_STATE["fail"] = True
    y.refresh(["AAPL", "SAP.DE"], "1h", force=True)
    assert "Yahoo nicht erreichbar" in y.last_error
    assert len(y.closed_candles("AAPL", "1h")) == 600  # alte Daten bleiben nutzbar
    calls = len(YAHOO_STATE["calls"])
    y.refresh(["AAPL"], "1h", force=True)
    assert len(YAHOO_STATE["calls"]) == calls       # während der Sperre keine neue Abfrage


def test_market_data_quotes_fx_and_open(cfg):
    cfg["universe"] = {"stocks": ["AAPL", "SAP.DE"]}
    u = build_universe(cfg, include_yahoo=True)
    md = MarketData(None, YahooSource(download=fake_yahoo_download))
    md.prepare(u, "1h", held=["AAPL"])
    price, fx = md.quote(u["AAPL"])
    assert fx == pytest.approx(1 / 1.10) and price > 0
    assert md.quote(u["SAP.DE"])[1] == 1.0
    assert md.is_open(u["AAPL"])
    YAHOO_STATE["closed"] = True
    md2 = MarketData(None, YahooSource(download=fake_yahoo_download))
    md2.prepare(u, "1h", held=["AAPL"])
    assert not md2.is_open(u["AAPL"])


# ------------------------------------------------------------ Scanner & Marktlage
def test_momentum_ranking():
    strong = add_indicators(extract(fake_yahoo_download(["A"], "", "1h"), "A").iloc[:300], {
        "ema_fast": 20, "ema_slow": 50, "ema_trend": 200, "rsi_period": 14, "rsi_max_entry": 70, "atr_period": 14})
    weak = strong.copy()
    weak["close"] = strong["close"].iloc[::-1].values
    assert momentum_score(strong) > momentum_score(weak)
    assert momentum_score(strong.iloc[:50]) is None
    ranks = rank_percentiles({"a": 3.0, "b": 1.0, "c": None, "d": 2.0})
    assert ranks == {"b": 0.0, "d": 0.5, "a": 1.0}


def daily(values):
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=len(values), freq="1D")
    return pd.DataFrame({"close": values}, index=idx)


def test_market_regime_states():
    up = daily(np.linspace(100, 200, 300))
    down = daily(np.linspace(200, 100, 300))
    cfg = {"vix_caution": 22, "vix_panic": 30}
    assert market_regime(up, daily([15.0]), cfg)["state"] == "risk_on"
    assert market_regime(up, daily([25.0]), cfg)["multiplier"] == 0.5
    assert market_regime(down, daily([15.0]), cfg)["state"] == "caution"
    assert market_regime(down, daily([25.0]), cfg)["state"] == "risk_off"
    assert market_regime(up, daily([35.0]), cfg)["multiplier"] == 0.0
    assert market_regime(up.iloc[:50], None, cfg)["state"] == "unknown"


# ------------------------------------------------------------ Handel über Anlageklassen
@pytest.fixture
def mbot(cfg, tmp_path, monkeypatch):
    from tests.test_bot import FakeExchange, SilentNotifier, patch_control
    from tradingbot.bot import TradingBot
    from tradingbot.data import synthetic

    patch_control(monkeypatch, tmp_path)
    cfg["symbols"] = ["BTC/EUR"]
    cfg["universe"] = {"stocks": ["AAPL", "SAP.DE"], "commodities": ["GC=F"]}
    cfg["scanner"] = {"top_pct": 1.0}
    cfg["regime"] = {"enabled": True, "vix_caution": 22, "vix_panic": 30}
    cfg["ai"] = {"mode": "off"}
    df = synthetic(3000)
    df.index = df.index - (df.index[-1] - pd.Timestamp.now(tz="UTC")) - pd.Timedelta(days=1)
    ex = FakeExchange(df)
    ex.cursor = 450
    yahoo = YahooSource(download=fake_yahoo_download)
    state = {}
    broker = PaperBroker(None, state, 1000, 0.001, 0.0)
    bot = TradingBot(cfg, ex, broker, StateStore(tmp_path / "s.json"), SilentNotifier(), state,
                     market=MarketData(ex, yahoo))
    return bot, ex


def force_entry_signal(bot, symbol):
    """Kaufsignal auf der letzten Kerze erzwingen (unabhängig von den Zufallsdaten)."""
    import tradingbot.bot as bot_module

    original = bot_module.add_indicators

    def patched(df, params):
        data = original(df, params)
        if bot.market.candles(bot.inst(symbol), bot.cfg["timeframe"], 10).index[-1] == df.index[-1] \
                and len(df) and abs(df["close"].iat[-1] - bot.market.yahoo.closed_candles(symbol, "1h")["close"].iat[-1]) < 1e-9:
            data.loc[data.index[-1], "entry"] = True
        return data
    return patched


def test_buys_us_stock_with_currency_conversion(mbot, monkeypatch):
    bot, _ = mbot
    import tradingbot.bot as bot_module
    monkeypatch.setattr(bot_module, "add_indicators", force_entry_signal(bot, "AAPL"))
    bot.step()
    pos = bot.state["positions"].get("AAPL")
    assert pos, bot.state.get("events")
    assert pos["currency"] == "USD" and pos["fx_entry"] == pytest.approx(1 / 1.10)
    spent = 1000 - bot.state["paper_cash"]
    assert spent == pytest.approx(pos["qty"] * pos["entry"] / 1.10 * 1.001, rel=1e-6)
    assert bot.state["regime"]["state"] == "risk_on"
    assert bot.state["market"]["GC=F"]["asset_class"] == "commodities"
    assert set(bot.state["market"]) >= {"AAPL", "SAP.DE", "GC=F", "BTC/EUR"}
    assert all(m.get("rank") is not None for m in bot.state["market"].values())


def test_no_entry_when_market_closed(mbot, monkeypatch):
    bot, _ = mbot
    import tradingbot.bot as bot_module
    YAHOO_STATE["closed"] = True
    monkeypatch.setattr(bot_module, "add_indicators", force_entry_signal(bot, "AAPL"))
    bot.step()
    assert "AAPL" not in bot.state["positions"]
    assert any("Markt gerade geschlossen" in e["text"] for e in bot.state["events"])


def test_risk_off_regime_blocks_entries(mbot, monkeypatch):
    bot, _ = mbot
    import tradingbot.bot as bot_module
    monkeypatch.setitem(PRICES, "^VIX", 40.0)
    monkeypatch.setattr(bot_module, "add_indicators", force_entry_signal(bot, "AAPL"))
    bot.step()
    assert bot.state["regime"]["state"] == "risk_off"
    assert "AAPL" not in bot.state["positions"]


def test_scanner_filter_blocks_weak_markets(mbot, monkeypatch):
    bot, _ = mbot
    import tradingbot.bot as bot_module
    bot.cfg["scanner"]["top_pct"] = 0.1
    monkeypatch.setattr(bot_module, "momentum_score", lambda data: 1.0 if data["close"].iat[-1] > 1000 else 0.1)
    monkeypatch.setattr(bot_module, "add_indicators", force_entry_signal(bot, "AAPL"))
    bot.step()
    assert "AAPL" not in bot.state["positions"]
    assert any("stärksten" in e["text"] for e in bot.state["events"])


def test_closed_market_defers_manual_exit(mbot, monkeypatch):
    bot, _ = mbot
    import tradingbot.bot as bot_module
    from tradingbot import control
    monkeypatch.setattr(bot_module, "add_indicators", force_entry_signal(bot, "AAPL"))
    bot.step()
    assert "AAPL" in bot.state["positions"]
    YAHOO_STATE["closed"] = True
    bot.market.yahoo.quote_fetched = 0
    control.queue_command("close", symbol="AAPL")
    bot.step()
    assert bot.state["positions"]["AAPL"]["pending_exit"] == "Manuell geschlossen"
    YAHOO_STATE["closed"] = False
    bot.market.yahoo.quote_fetched = 0
    bot.step()
    assert "AAPL" not in bot.state["positions"]
    assert bot.state["closed_trades"][-1]["currency"] == "USD"


@pytest.mark.parametrize("decision, mode, bought", [
    ("reject", "veto", False), ("approve", "veto", True), ("reject", "advisory", True)])
def test_ai_review_in_bot(mbot, monkeypatch, decision, mode, bought):
    import json

    import tradingbot.bot as bot_module
    from tests.test_ai import FakeClient, text_response
    from tradingbot.ai import AIAnalyst

    bot, _ = mbot
    client = FakeClient([text_response(json.dumps(
        {"decision": decision, "confidence": "mittel", "reason": "Begründung X", "risks": []}))])
    bot.ai = AIAnalyst({"mode": mode, "brief_hour_utc": 24}, "", bot.state, client=client)
    monkeypatch.setattr(bot_module, "add_indicators", force_entry_signal(bot, "AAPL"))
    bot.step()
    assert ("AAPL" in bot.state["positions"]) is bought
    context = json.loads(client.calls[0]["messages"][0]["content"].split("(JSON):\n", 1)[1])
    assert context["instrument"]["symbol"] == "AAPL" and context["marktlage"]["label"] == "Freundlich"
    assert context["signal"]["stop"] < context["signal"]["kurs"]
    assert bot.state["ai"]["decisions"][-1]["decision"] == decision
    assert bool(bot.state["ai"]["shadow"]) is (decision == "reject")
    assert any("KI" in e["text"] and "AAPL" in e["text"] for e in bot.state["events"])
