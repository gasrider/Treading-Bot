import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

VALID_MODES = ("paper", "live")
TIMEFRAMES = ("15m", "30m", "1h", "4h", "1d")

# Im Dashboard geänderte Einstellungen. Sie überschreiben config.yaml, ohne die Datei
# (mit ihren Kommentaren) anzufassen. Löschen = zurück zu config.yaml.
SETTINGS_OVERRIDE = Path("state") / "settings.yaml"

# Was über das Dashboard geändert werden darf – mit Grenzen. Modus, Börse und
# Schlüssel bewusst NICHT: die lassen sich nur direkt auf dem Server ändern.
# (Abschnitt, Schlüssel, Bezeichnung, Typ, Minimum, Maximum, Prozentanzeige)
EDITABLE = [
    ("strategy", "ema_fast", "Schnelle EMA (Kerzen)", int, 3, 100, False),
    ("strategy", "ema_slow", "Langsame EMA (Kerzen)", int, 5, 300, False),
    ("strategy", "ema_trend", "Trend-EMA (Kerzen)", int, 20, 400, False),
    ("strategy", "rsi_max_entry", "Kein Kauf über RSI", float, 50, 100, False),
    ("strategy", "stop_atr", "Stop-Loss (× ATR)", float, 0.5, 10, False),
    ("strategy", "take_profit_atr", "Take-Profit (× ATR, 0 = aus)", float, 0, 20, False),
    ("strategy", "trailing_atr", "Nachziehender Stop (× ATR, 0 = aus)", float, 0, 10, False),
    ("risk", "risk_per_trade", "Risiko pro Trade", float, 0.001, 0.03, True),
    ("risk", "max_position_pct", "Max. Positionsgröße", float, 0.05, 1.0, True),
    ("risk", "max_open_positions", "Max. offene Positionen", int, 1, 10, False),
    ("risk", "max_daily_loss", "Tagesverlust-Limit", float, 0.01, 0.2, True),
    ("risk", "max_drawdown", "Notbremse bei Drawdown", float, 0.05, 0.5, True),
]


def validate(cfg: dict) -> None:
    """Wirft ValueError mit verständlicher Meldung, wenn die Konfiguration nicht passt."""
    if cfg.get("mode") not in VALID_MODES:
        raise ValueError(f"mode muss eines von {VALID_MODES} sein, nicht {cfg.get('mode')!r}")
    if not cfg.get("symbols"):
        raise ValueError("Mindestens ein Handelspaar auswählen")
    quotes = {s.split("/")[1] for s in cfg["symbols"]}
    if len(quotes) != 1:
        raise ValueError(f"Alle Paare brauchen dieselbe Quote-Währung, gefunden: {sorted(quotes)}")
    if cfg["timeframe"] not in TIMEFRAMES:
        raise ValueError(f"timeframe muss eines von {TIMEFRAMES} sein")
    for section, key, label, _, lo, hi, pct in EDITABLE:
        value = cfg[section][key]
        if not lo <= value <= hi:
            fmt = (lambda v: f"{v * 100:g} %") if pct else (lambda v: f"{v:g}")
            raise ValueError(f"{label} muss zwischen {fmt(lo)} und {fmt(hi)} liegen")
    s = cfg["strategy"]
    if not s["ema_fast"] < s["ema_slow"] <= s["ema_trend"]:
        raise ValueError("Es muss gelten: schnelle EMA < langsame EMA ≤ Trend-EMA")


def apply_overrides(cfg: dict, overrides: dict) -> dict:
    """Nur erlaubte Schlüssel übernehmen (siehe EDITABLE, symbols, timeframe)."""
    if "symbols" in overrides:
        cfg["symbols"] = list(overrides["symbols"])
    if "timeframe" in overrides:
        cfg["timeframe"] = overrides["timeframe"]
    for section, key, *_ in EDITABLE:
        if key in overrides.get(section, {}):
            cfg[section][key] = overrides[section][key]
    return cfg


def load_overrides() -> dict:
    if SETTINGS_OVERRIDE.exists():
        return yaml.safe_load(SETTINGS_OVERRIDE.read_text(encoding="utf-8")) or {}
    return {}


def config_files(path="config.yaml") -> list[Path]:
    return [Path(path), SETTINGS_OVERRIDE]


def load_config(path="config.yaml"):
    load_dotenv()
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cfg = apply_overrides(cfg, load_overrides())
    validate(cfg)
    cfg["quote"] = cfg["symbols"][0].split("/")[1]
    cfg["secrets"] = {
        "api_key": os.getenv("EXCHANGE_API_KEY", ""),
        "api_secret": os.getenv("EXCHANGE_API_SECRET", ""),
        "live_confirm": os.getenv("LIVE_TRADING_CONFIRM", ""),
        "telegram_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    }
    return cfg
