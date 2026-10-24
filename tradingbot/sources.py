"""Kursdaten aus mehreren Quellen: Krypto-Börse (ccxt) und Yahoo Finance (alles andere).

Yahoo hat keine offizielle Schnittstelle und sperrt bei zu vielen Abfragen zeitweise.
Darum werden alle Ticker gebündelt in EINER Abfrage geholt, zwischengespeichert und bei
Fehlern mit wachsendem Abstand erneut versucht – der Bot arbeitet dann mit den letzten
guten Daten weiter.
"""
import logging
import time
import warnings

import pandas as pd

from .data import fetch_recent
from .markets import Instrument, fx_ticker

log = logging.getLogger(__name__)

# Intervall -> (Yahoo-Intervall, Zeitraum, Umrechnung)
YAHOO_TF = {
    "15m": ("15m", "59d", None),
    "30m": ("30m", "59d", None),
    "1h": ("1h", "180d", None),
    "4h": ("1h", "720d", "4h"),
    "1d": ("1d", "3y", None),
}
TF_SECONDS = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}


def yf_download(tickers: list[str], period: str, interval: str) -> pd.DataFrame:
    import yfinance as yf  # erst hier laden: nur nötig, wenn Yahoo-Märkte aktiv sind

    with warnings.catch_warnings():  # yfinance nutzt veraltete pandas-Aufrufe -> Log nicht fluten
        warnings.simplefilter("ignore")
        return yf.download(tickers, period=period, interval=interval, group_by="ticker",
                           auto_adjust=True, progress=False, threads=False)


def extract(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Einen Ticker aus dem gebündelten Yahoo-Ergebnis holen (Spalten klein, Index UTC)."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(0):
            return pd.DataFrame()
        df = raw[ticker].copy()
    else:
        df = raw.copy()
    df.columns = [str(c).lower() for c in df.columns]
    need = ["open", "high", "low", "close"]
    if not set(need) <= set(df.columns):
        return pd.DataFrame()
    df = df.dropna(subset=need)
    if "volume" not in df.columns:
        df["volume"] = 0.0
    idx = pd.DatetimeIndex(df.index)
    df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    df.index.name = "timestamp"
    return df[need + ["volume"]].astype(float).sort_index()


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.resample(rule, label="left", closed="left", origin="epoch").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return out.dropna(subset=["open", "close"])


def drop_forming(df: pd.DataFrame, tf: str, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Nur abgeschlossene Kerzen behalten."""
    if df.empty:
        return df
    now = now or pd.Timestamp.now(tz="UTC")
    if df.index[-1] + pd.Timedelta(seconds=TF_SECONDS[tf]) > now:
        return df.iloc[:-1]
    return df


class YahooSource:
    def __init__(self, download=yf_download, candle_ttl: int = 900, quote_ttl: int = 120):
        self.download = download
        self.candle_ttl = candle_ttl
        self.quote_ttl = quote_ttl
        self.candles: dict[tuple[str, str], pd.DataFrame] = {}
        self.candle_fetched: dict[str, float] = {}
        self.quotes: dict[str, tuple[float, pd.Timestamp, float]] = {}  # Kurs, Kerzenzeit, abgerufen
        self.quote_fetched = 0.0
        self.quote_set: frozenset = frozenset()
        self.blocked_until = 0.0
        self.backoff = 300
        self.last_error = ""

    def _call(self, tickers: list[str], period: str, interval: str) -> pd.DataFrame | None:
        if time.time() < self.blocked_until:
            return None
        try:
            raw = self.download(sorted(set(tickers)), period, interval)
        except Exception as e:  # Sperre/Netzwerk: später erneut versuchen
            self.blocked_until = time.time() + self.backoff
            self.last_error = f"Yahoo nicht erreichbar ({e.__class__.__name__}) – neuer Versuch in {self.backoff // 60} Min."
            log.warning(self.last_error)
            self.backoff = min(self.backoff * 2, 3600)
            return None
        self.backoff = 300
        self.last_error = ""
        return raw

    def refresh(self, tickers: list[str], tf: str, force: bool = False) -> None:
        """Kerzen aller Ticker gebündelt laden (höchstens alle candle_ttl Sekunden)."""
        if not tickers:
            return
        fresh = time.time() - self.candle_fetched.get(tf, 0) < self.candle_ttl
        if fresh and not force and all((t, tf) in self.candles for t in tickers):
            return
        interval, period, rule = YAHOO_TF[tf]
        raw = self._call(tickers, period, interval)
        if raw is None:
            return
        for t in tickers:
            df = extract(raw, t)
            if rule:
                df = resample(df, rule)
            if not df.empty:
                self.candles[(t, tf)] = df
        self.candle_fetched[tf] = time.time()

    def refresh_quotes(self, tickers: list[str]) -> None:
        """Aktuelle Kurse (5-Minuten-Kerzen) für offene Positionen und Wechselkurse."""
        tickers = sorted(set(tickers))
        if not tickers:
            return
        if (time.time() - self.quote_fetched < self.quote_ttl
                and frozenset(tickers) <= self.quote_set):
            return
        raw = self._call(tickers, "5d", "5m")
        if raw is None:
            return
        for t in tickers:
            df = extract(raw, t)
            if not df.empty:
                self.quotes[t] = (float(df["close"].iat[-1]), df.index[-1], time.time())
        self.quote_fetched = time.time()
        self.quote_set = frozenset(tickers)

    def history(self, ticker: str, tf: str) -> pd.DataFrame:
        interval, period, rule = YAHOO_TF[tf]
        if interval in ("1h",):
            period = "730d"
        raw = self._call([ticker], period, interval)
        df = extract(raw, ticker) if raw is not None else pd.DataFrame()
        return resample(df, rule) if rule else df

    def closed_candles(self, ticker: str, tf: str) -> pd.DataFrame:
        return drop_forming(self.candles.get((ticker, tf), pd.DataFrame()), tf)

    def all_candles(self, ticker: str, tf: str) -> pd.DataFrame:
        return self.candles.get((ticker, tf), pd.DataFrame())

    def _latest_candle(self, ticker: str) -> tuple[float, pd.Timestamp, int] | None:
        """Jüngste (auch noch laufende) Kerze über alle geladenen Intervalle: Kurs, Start, Länge."""
        best = None
        for (t, tf), df in self.candles.items():
            if t == ticker and not df.empty and (best is None or df.index[-1] > best[1]):
                best = (float(df["close"].iat[-1]), df.index[-1], TF_SECONDS[tf])
        return best

    def price(self, ticker: str) -> float | None:
        """Aktueller 5-Minuten-Kurs, sonst Schlusskurs der jüngsten Kerze."""
        q = self.quotes.get(ticker)
        if q and time.time() - q[2] < 900:
            return q[0]
        latest = self._latest_candle(ticker)
        return latest[0] if latest else (q[0] if q else None)

    def is_open(self, ticker: str, now: pd.Timestamp | None = None) -> bool:
        """Markt gilt als offen, wenn die letzte Kursänderung höchstens 30 Minuten alt ist."""
        now = now or pd.Timestamp.now(tz="UTC")
        q = self.quotes.get(ticker)
        if q and time.time() - q[2] < 900:
            return now - q[1] <= pd.Timedelta(minutes=30)
        latest = self._latest_candle(ticker)
        if not latest:
            return False
        return now - latest[1] <= pd.Timedelta(seconds=latest[2]) + pd.Timedelta(minutes=30)


class MarketData:
    """Ein Zugang zu allen Kursen – egal ob Krypto-Börse oder Yahoo."""

    def __init__(self, exchange, yahoo: YahooSource | None, base_currency: str = "EUR"):
        self.exchange = exchange
        self.yahoo = yahoo
        self.base = base_currency
        self.crypto_prices: dict[str, float] = {}

    def prepare(self, universe: dict[str, Instrument], tf: str, held: list[str]) -> None:
        """Einmal pro Durchlauf: Yahoo gebündelt aktualisieren."""
        if not self.yahoo:
            return
        yahoo_syms = [s for s, i in universe.items() if i.source == "yahoo"]
        self.yahoo.refresh(yahoo_syms, tf)
        currencies = {i.currency for i in universe.values()}
        fx = [t for t in (fx_ticker(c, self.base) for c in currencies) if t]
        held_yahoo = [s for s in held if s in universe and universe[s].source == "yahoo"]
        self.yahoo.refresh_quotes(held_yahoo + fx)

    def candles(self, inst: Instrument, tf: str, limit: int) -> pd.DataFrame:
        if inst.source == "exchange":
            return fetch_recent(self.exchange, inst.symbol, tf, limit=limit)
        if not self.yahoo:
            return pd.DataFrame()
        return self.yahoo.closed_candles(inst.symbol, tf).iloc[-limit:]

    def price(self, inst: Instrument) -> float | None:
        if inst.source == "exchange":
            price = float(self.exchange.fetch_ticker(inst.symbol)["last"])
            self.crypto_prices[inst.symbol] = price
            return price
        return self.yahoo.price(inst.symbol) if self.yahoo else None

    def fx(self, currency: str) -> float | None:
        """Wie viele EUR ist 1 Einheit `currency` wert?"""
        if currency == self.base:
            return 1.0
        rate = self.yahoo.price(fx_ticker(currency, self.base)) if self.yahoo else None
        return 1.0 / rate if rate else None

    def quote(self, inst: Instrument) -> tuple[float, float]:
        """(Kurs in Notierungswährung, Umrechnungsfaktor in EUR) – wirft, wenn unbekannt."""
        price, fx = self.price(inst), self.fx(inst.currency)
        if price is None or fx is None:
            raise LookupError(f"{inst.symbol}: kein aktueller Kurs/Wechselkurs verfügbar")
        return price, fx

    def is_open(self, inst: Instrument) -> bool:
        if inst.source == "exchange":
            return True
        return bool(self.yahoo and self.yahoo.is_open(inst.symbol))
