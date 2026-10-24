"""Marktscanner: bewertet alle Märkte und erkennt die Gesamtmarktlage.

Bewertung ("relative Stärke"): gewichtete Rendite der letzten 20/60/120 Kerzen geteilt
durch die typische Schwankung (ATR in %). Hohe Werte = starker, ruhiger Aufwärtstrend.
Gekauft wird nur, wenn ein Kaufsignal UND eine gute Platzierung im Ranking vorliegen.

Marktlage: S&P 500 über/unter seiner 200-Tage-Linie und Volatilitätsindex VIX
bestimmen, mit wie viel Risiko gehandelt wird (100 % / 50 % / gar nicht).
"""
import math

import pandas as pd

from .indicators import ema

REGIME_LABELS = {
    "risk_on": "Freundlich",
    "caution": "Vorsicht",
    "risk_off": "Risiko aus",
    "unknown": "Unbekannt",
}


def momentum_score(data: pd.DataFrame) -> float | None:
    """Risikobereinigtes Momentum aus einer Tabelle mit close und atr."""
    if len(data) < 121:
        return None
    close = data["close"]
    r20 = close.iat[-1] / close.iat[-21] - 1
    r60 = close.iat[-1] / close.iat[-61] - 1
    r120 = close.iat[-1] / close.iat[-121] - 1
    atr_pct = float(data["atr"].iat[-1] / close.iat[-1])
    if not atr_pct or math.isnan(atr_pct):
        return None
    score = (0.5 * r20 + 0.3 * r60 + 0.2 * r120) / (atr_pct * 10)
    return round(float(score), 3)


def rank_percentiles(scores: dict[str, float | None]) -> dict[str, float]:
    """1.0 = stärkster Markt, 0.0 = schwächster (nur Märkte mit Bewertung)."""
    valid = sorted((s for s in scores.items() if s[1] is not None), key=lambda kv: kv[1])
    n = len(valid)
    if n == 0:
        return {}
    if n == 1:
        return {valid[0][0]: 1.0}
    return {sym: round(i / (n - 1), 3) for i, (sym, _) in enumerate(valid)}


def market_regime(index_daily: pd.DataFrame, vix_daily: pd.DataFrame, cfg: dict) -> dict:
    """Gesamtmarktlage aus Tagesdaten des Leitindex (S&P 500) und des VIX."""
    caution, panic = cfg.get("vix_caution", 22), cfg.get("vix_panic", 30)
    if index_daily is None or len(index_daily) < 200:
        return {"state": "unknown", "multiplier": 1.0, "label": REGIME_LABELS["unknown"],
                "text": "Keine ausreichenden Daten zum Gesamtmarkt – der Bot handelt normal."}
    close = index_daily["close"]
    trend = ema(close, 200)
    above = bool(close.iat[-1] > trend.iat[-1])
    dist = float(close.iat[-1] / trend.iat[-1] - 1) * 100
    vix = float(vix_daily["close"].iat[-1]) if vix_daily is not None and len(vix_daily) else None
    if vix is not None and vix >= panic:
        state = "risk_off"
    elif above and (vix is None or vix < caution):
        state = "risk_on"
    elif not above and vix is not None and vix >= caution:
        state = "risk_off"
    else:
        state = "caution"
    multiplier = {"risk_on": 1.0, "caution": 0.5, "risk_off": 0.0}[state]
    vix_txt = f"VIX {vix:.1f}" if vix is not None else "VIX unbekannt"
    trend_txt = f"S&P 500 {'über' if above else 'unter'} der 200-Tage-Linie ({dist:+.1f} %)"
    effect = {"risk_on": "voller Einsatz", "caution": "halbes Risiko pro Trade",
              "risk_off": "keine neuen Käufe"}[state]
    return {"state": state, "multiplier": multiplier, "label": REGIME_LABELS[state],
            "text": f"{trend_txt}, {vix_txt} → {effect}.", "vix": vix, "index_vs_trend_pct": round(dist, 2)}
