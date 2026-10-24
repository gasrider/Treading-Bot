import ccxt
import pytest
import yaml

from tradingbot import config as config_module
from tradingbot.config import apply_overrides, load_config, validate
from tests.test_bot import FakeExchange, SilentNotifier, setup  # noqa: F401 (Fixture)


@pytest.fixture
def config_file(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "SETTINGS_OVERRIDE", tmp_path / "settings.yaml")
    path = tmp_path / "config.yaml"
    base = {k: v for k, v in cfg.items() if k not in ("quote", "secrets")}
    path.write_text(yaml.safe_dump(base))
    return path


def test_overrides_only_touch_allowed_keys(cfg):
    out = apply_overrides(cfg, {"mode": "live", "exchange": "evil", "symbols": ["SOL/EUR"],
                                "risk": {"risk_per_trade": 0.02, "unbekannt": 1}})
    assert out["mode"] == "paper" and out["exchange"] == "kraken"
    assert out["symbols"] == ["SOL/EUR"] and out["risk"]["risk_per_trade"] == 0.02
    assert "unbekannt" not in out["risk"]


@pytest.mark.parametrize("change, message", [
    ({"risk": {"risk_per_trade": 0.2}}, "Risiko pro Trade"),
    ({"strategy": {"ema_fast": 60}}, "schnelle EMA"),
    ({"symbols": ["BTC/EUR", "ETH/USD"]}, "Quote-Währung"),
    ({"symbols": [], "universe": {}}, "Mindestens einen Markt"),
    ({"timeframe": "7m"}, "timeframe"),
])
def test_validation_messages(cfg, change, message):
    if "universe" in change:
        cfg["universe"] = change.pop("universe")
    with pytest.raises(ValueError, match=message):
        validate(apply_overrides(cfg, change))


def test_load_config_merges_dashboard_settings(config_file):
    config_module.SETTINGS_OVERRIDE.write_text(yaml.safe_dump({"timeframe": "4h", "symbols": ["SOL/EUR"]}))
    cfg = load_config(config_file)
    assert cfg["timeframe"] == "4h" and cfg["symbols"] == ["SOL/EUR"] and cfg["quote"] == "EUR"


def test_bot_hot_reloads_settings(setup, config_file):
    bot, ex, notifier, _, first_entry, _ = setup
    bot.config_path = config_file
    bot._config_stamp = bot._stamp()
    ex.cursor = first_entry + 1
    config_module.SETTINGS_OVERRIDE.write_text(yaml.safe_dump({"risk": {"risk_per_trade": 0.005}}))
    bot.step()
    assert bot.cfg["risk"]["risk_per_trade"] == 0.005
    assert bot.guard.cfg["risk_per_trade"] == 0.005
    assert any("Neue Einstellungen" in m for m in notifier.messages)

    config_module.SETTINGS_OVERRIDE.write_text(yaml.safe_dump({"risk": {"risk_per_trade": 0.5}}))
    bot.step()  # ungültig -> alte Werte bleiben
    assert bot.cfg["risk"]["risk_per_trade"] == 0.005
    assert any("nicht übernommen" in m for m in notifier.messages)


def test_market_snapshot_written(setup):
    bot, ex, _, _, first_entry, _ = setup
    ex.cursor = first_entry + 1
    bot.step()
    m = bot.state["market"]["BTC/EUR"]
    assert len(m["candles"]) == 96 and len(m["ema_trend"]) == 96
    assert m["entry_signal"] and m["uptrend"]
    assert m["price"] == pytest.approx(ex.df["close"].iat[first_entry], rel=1e-5)


def test_bad_symbol_does_not_block_others(setup):
    bot, ex, _, _, first_entry, _ = setup
    original = ex.fetch_ohlcv

    def fetch(symbol, *a, **k):
        if symbol == "FAKE/EUR":
            raise ccxt.BadSymbol("kraken does not have market symbol FAKE/EUR")
        return original(symbol, *a, **k)

    ex.fetch_ohlcv = fetch
    bot.cfg["symbols"] = ["FAKE/EUR", "BTC/EUR"]
    bot.universe = bot._build_universe()
    ex.cursor = first_entry + 1
    bot.step()
    assert "BTC/EUR" in bot.state["positions"]
    assert "FAKE/EUR" in bot.state["market"]["FAKE/EUR"]["error"]
