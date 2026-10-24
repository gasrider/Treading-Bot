import copy
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .markets import ASSET_CLASSES, DEFAULT_UNIVERSE, valid_yahoo_ticker

VALID_MODES = ("paper", "live")
AI_MODES = ("off", "advisory", "veto")
UNIVERSE_CLASSES = [c for c in ASSET_CLASSES if c != "crypto"]
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
    ("scanner", "top_pct", "Nur die stärksten … kaufen", float, 0.1, 1.0, True),
]


# Fehlen diese Abschnitte in einer älteren config.yaml, gelten diese Werte.
DEFAULT_SECTIONS = {
    "universe": DEFAULT_UNIVERSE,
    "scanner": {"top_pct": 0.5},
    "regime": {"enabled": True, "vix_caution": 22, "vix_panic": 30},
    "ai": {"mode": "veto"},
    # Gebühr + Spread/Slippage pro Order je Anlageklasse (Schätzwerte; Krypto: siehe paper/backtest)
    "costs": {
        "stocks": {"fee": 0.001, "slippage": 0.0005},
        "etfs": {"fee": 0.001, "slippage": 0.0003},
        "commodities": {"fee": 0.0005, "slippage": 0.0003},
        "forex": {"fee": 0.0, "slippage": 0.0001},
    },
}


def trading_costs(cfg: dict, asset_class: str, section: str = "paper") -> tuple[float, float]:
    """(Gebühr, Slippage) pro Order für eine Anlageklasse."""
    c = (cfg.get("costs") or {}).get(asset_class)
    if c:
        return float(c["fee"]), float(c["slippage"])
    return float(cfg[section]["fee"]), float(cfg[section]["slippage"])


def fill_defaults(cfg: dict) -> dict:
    for key, default in DEFAULT_SECTIONS.items():
        if cfg.get(key) is None:
            cfg[key] = copy.deepcopy(default)
        elif isinstance(default, dict) and key != "universe":
            for sub, value in default.items():
                cfg[key].setdefault(sub, copy.deepcopy(value))
    return cfg


def validate(cfg: dict) -> None:
    """Wirft ValueError mit verständlicher Meldung, wenn die Konfiguration nicht passt."""
    if cfg.get("mode") not in VALID_MODES:
        raise ValueError(f"mode muss eines von {VALID_MODES} sein, nicht {cfg.get('mode')!r}")
    cfg["symbols"] = list(cfg.get("symbols") or [])
    if not cfg["symbols"] and not any((cfg.get("universe") or {}).values()):
        raise ValueError("Mindestens einen Markt auswählen")
    quotes = {s.split("/")[1] for s in cfg["symbols"]}
    if len(quotes) > 1:
        raise ValueError(f"Alle Paare brauchen dieselbe Quote-Währung, gefunden: {sorted(quotes)}")
    if cfg["timeframe"] not in TIMEFRAMES:
        raise ValueError(f"timeframe muss eines von {TIMEFRAMES} sein")
    for asset_class, tickers in (cfg.get("universe") or {}).items():
        if asset_class not in UNIVERSE_CLASSES:
            raise ValueError(f"Unbekannte Anlageklasse „{asset_class}“ (erlaubt: {', '.join(UNIVERSE_CLASSES)})")
        for t in tickers or []:
            if not valid_yahoo_ticker(str(t)):
                raise ValueError(f"Ungültiger oder nicht unterstützter Ticker „{t}“")
    if (cfg.get("ai") or {}).get("mode", "off") not in AI_MODES:
        raise ValueError(f"ai.mode muss eines von {AI_MODES} sein")
    for section, key, label, _, lo, hi, pct in EDITABLE:
        value = cfg[section][key]
        if not lo <= value <= hi:
            fmt = (lambda v: f"{v * 100:g} %") if pct else (lambda v: f"{v:g}")
            raise ValueError(f"{label} muss zwischen {fmt(lo)} und {fmt(hi)} liegen")
    s = cfg["strategy"]
    if not s["ema_fast"] < s["ema_slow"] <= s["ema_trend"]:
        raise ValueError("Es muss gelten: schnelle EMA < langsame EMA ≤ Trend-EMA")


def apply_overrides(cfg: dict, overrides: dict) -> dict:
    """Nur erlaubte Schlüssel übernehmen (EDITABLE, symbols, timeframe, universe, ai.mode)."""
    cfg.setdefault("scanner", {}).setdefault("top_pct", 1.0)
    if isinstance(overrides.get("universe"), dict):
        cfg["universe"] = {c: list(overrides["universe"].get(c) or []) for c in UNIVERSE_CLASSES}
    if (overrides.get("ai") or {}).get("mode") is not None:
        cfg.setdefault("ai", {})["mode"] = overrides["ai"]["mode"]
    if "symbols" in overrides:
        cfg["symbols"] = list(overrides["symbols"])
    if "timeframe" in overrides:
        cfg["timeframe"] = overrides["timeframe"]
    for section, key, *_ in EDITABLE:
        if key in (overrides.get(section) or {}):
            cfg.setdefault(section, {})[key] = overrides[section][key]
    return cfg


def load_overrides() -> dict:
    if SETTINGS_OVERRIDE.exists():
        return yaml.safe_load(SETTINGS_OVERRIDE.read_text(encoding="utf-8")) or {}
    return {}


def config_files(path="config.yaml") -> list[Path]:
    return [Path(path), SETTINGS_OVERRIDE]


def load_config(path="config.yaml"):
    load_dotenv()
    cfg = fill_defaults(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    cfg = apply_overrides(cfg, load_overrides())
    validate(cfg)
    # Konto-Währung: Quote-Währung der Krypto-Paare, sonst EUR
    cfg["quote"] = cfg["symbols"][0].split("/")[1] if cfg["symbols"] else cfg.get("account_currency", "EUR")
    cfg["secrets"] = {
        "api_key": os.getenv("EXCHANGE_API_KEY", ""),
        "api_secret": os.getenv("EXCHANGE_API_SECRET", ""),
        "live_confirm": os.getenv("LIVE_TRADING_CONFIRM", ""),
        "telegram_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
    }
    return cfg
