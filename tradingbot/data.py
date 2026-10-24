import time
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def make_exchange(name: str, api_key: str = "", api_secret: str = ""):
    if not hasattr(ccxt, name):
        raise ValueError(f"Unbekannte Börse: {name}")
    params = {"enableRateLimit": True}
    if api_key:
        params.update(apiKey=api_key, secret=api_secret)
    return getattr(ccxt, name)(params)


def to_frame(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.drop_duplicates("timestamp").set_index("timestamp").sort_index().astype(float)


def fetch_recent(exchange, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
    """Letzte Kerzen holen und die noch nicht abgeschlossene Kerze entfernen."""
    df = to_frame(exchange.fetch_ohlcv(symbol, timeframe, limit=limit))
    tf_ms = exchange.parse_timeframe(timeframe) * 1000
    now = pd.Timestamp.now(tz="UTC")
    if len(df) and df.index[-1] + pd.Timedelta(milliseconds=tf_ms) > now:
        df = df.iloc[:-1]
    return df


def fetch_history(exchange, symbol: str, timeframe: str, days: int, cache_dir="data") -> pd.DataFrame:
    """Historische Kerzen seitenweise laden (mit CSV-Cache)."""
    cache = Path(cache_dir) / f"{exchange.id}_{symbol.replace('/', '-')}_{timeframe}_{days}d.csv"
    if cache.exists() and time.time() - cache.stat().st_mtime < 6 * 3600:
        return pd.read_csv(cache, index_col="timestamp", parse_dates=True)
    tf_ms = exchange.parse_timeframe(timeframe) * 1000
    since = exchange.milliseconds() - days * 86_400_000
    rows = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        since = batch[-1][0] + tf_ms
        if since >= exchange.milliseconds() - tf_ms:
            break
    if not rows:
        raise RuntimeError(f"Keine Daten für {symbol} auf {exchange.id}")
    df = to_frame(rows)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache)
    return df


def synthetic(bars: int = 5000, seed: int = 42, start_price: float = 30000.0) -> pd.DataFrame:
    """Künstliche Kursdaten mit Trendphasen – für Tests und Offline-Demo."""
    rng = np.random.default_rng(seed)
    regime = np.repeat(rng.normal(0, 0.0012, bars // 250 + 1), 250)[:bars]
    returns = regime + rng.normal(0, 0.008, bars)
    close = start_price * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[start_price], close[:-1]])
    spread = np.abs(rng.normal(0, 0.004, bars)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    idx = pd.date_range("2022-01-01", periods=bars, freq="h", tz="UTC", name="timestamp")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx
    )
