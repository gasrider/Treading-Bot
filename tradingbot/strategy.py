"""Trendfolge-Strategie: EMA-Kreuzung, gefiltert durch langfristigen Trend und RSI.

Einstieg (nur Long, kein Hebel): schnelle EMA kreuzt die langsame EMA nach oben,
der Schlusskurs liegt über der Trend-EMA und der RSI ist nicht überkauft.
Ausstieg: Stop-Loss, Take-Profit, nachziehender Stop oder Kreuzung nach unten.
"""
from dataclasses import dataclass

import pandas as pd

from .indicators import atr, ema, rsi


@dataclass
class Signal:
    entry: bool
    exit: bool
    close: float
    atr: float
    high: float


def warmup_bars(params: dict) -> int:
    return max(params["ema_trend"], params["ema_slow"], params["atr_period"]) + 2


def add_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = ema(df["close"], params["ema_fast"])
    df["ema_slow"] = ema(df["close"], params["ema_slow"])
    df["ema_trend"] = ema(df["close"], params["ema_trend"])
    df["rsi"] = rsi(df["close"], params["rsi_period"])
    df["atr"] = atr(df, params["atr_period"])
    above = df["ema_fast"] > df["ema_slow"]
    prev_above = above.shift(1, fill_value=False).astype(bool)
    df["cross_up"] = above & ~prev_above
    df["cross_down"] = ~above & prev_above
    df["entry"] = (
        df["cross_up"] & (df["close"] > df["ema_trend"]) & (df["rsi"] < params["rsi_max_entry"])
    )
    df["exit"] = df["cross_down"]
    return df


def signal_at(df: pd.DataFrame, i: int) -> Signal:
    row = df.iloc[i]
    return Signal(
        entry=bool(row["entry"]),
        exit=bool(row["exit"]),
        close=float(row["close"]),
        atr=float(row["atr"]),
        high=float(row["high"]),
    )


def initial_levels(price: float, atr_value: float, params: dict):
    stop = price - params["stop_atr"] * atr_value
    tp = price + params["take_profit_atr"] * atr_value if params["take_profit_atr"] > 0 else None
    return stop, tp


def trail_stop(current_stop: float, highest: float, atr_value: float, params: dict) -> float:
    if params["trailing_atr"] <= 0:
        return current_stop
    return max(current_stop, highest - params["trailing_atr"] * atr_value)
