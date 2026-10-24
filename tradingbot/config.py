import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

VALID_MODES = ("paper", "live")


def load_config(path="config.yaml"):
    load_dotenv()
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if cfg.get("mode") not in VALID_MODES:
        raise ValueError(f"mode muss eines von {VALID_MODES} sein, nicht {cfg.get('mode')!r}")
    quotes = {s.split("/")[1] for s in cfg["symbols"]}
    if len(quotes) != 1:
        raise ValueError(f"Alle Symbole brauchen dieselbe Quote-Währung, gefunden: {quotes}")
    cfg["quote"] = quotes.pop()
    s = cfg["strategy"]
    if not s["ema_fast"] < s["ema_slow"] <= s["ema_trend"]:
        raise ValueError("Es muss gelten: ema_fast < ema_slow <= ema_trend")
    r = cfg["risk"]
    if not 0 < r["risk_per_trade"] <= 0.05:
        raise ValueError("risk_per_trade muss zwischen 0 und 0.05 (5 %) liegen")
    cfg["secrets"] = {
        "api_key": os.getenv("EXCHANGE_API_KEY", ""),
        "api_secret": os.getenv("EXCHANGE_API_SECRET", ""),
        "live_confirm": os.getenv("LIVE_TRADING_CONFIRM", ""),
        "telegram_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    }
    return cfg
