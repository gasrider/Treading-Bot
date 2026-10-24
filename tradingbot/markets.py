"""Welche Märkte der Bot beobachtet: Krypto (Börse) und Aktien, ETFs, Rohstoffe, Devisen (Yahoo).

Jedes Instrument hat eine Datenquelle, eine Kurs-Währung und die Info, ob es rund um
die Uhr gehandelt wird. Der Paper-Broker rechnet alles in die Konto-Währung (EUR) um.
"""
import re
from dataclasses import dataclass

ASSET_CLASSES = {
    "crypto": "Krypto",
    "stocks": "Aktien",
    "etfs": "ETFs",
    "commodities": "Rohstoffe",
    "forex": "Devisen",
}

# Bekannte Namen für die Anzeige; unbekannte Ticker funktionieren trotzdem.
NAMES = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "AMZN": "Amazon", "GOOGL": "Alphabet",
    "META": "Meta", "TSLA": "Tesla", "JPM": "JPMorgan", "V": "Visa", "BRK-B": "Berkshire Hathaway",
    "SAP.DE": "SAP", "SIE.DE": "Siemens", "ALV.DE": "Allianz", "ASML.AS": "ASML", "MC.PA": "LVMH",
    "OMV.VI": "OMV", "EBS.VI": "Erste Group", "VER.VI": "Verbund", "NESN.SW": "Nestlé", "NOVO-B.CO": "Novo Nordisk",
    "SPY": "S&P 500 ETF", "QQQ": "Nasdaq-100 ETF", "IWDA.AS": "MSCI World ETF", "EXS1.DE": "DAX ETF",
    "EUNL.DE": "MSCI World ETF (Xetra)", "VWCE.DE": "FTSE All-World ETF", "EEM": "Schwellenländer ETF",
    "GC=F": "Gold", "SI=F": "Silber", "CL=F": "Rohöl WTI", "BZ=F": "Rohöl Brent", "NG=F": "Erdgas",
    "HG=F": "Kupfer", "PL=F": "Platin", "ZW=F": "Weizen",
    "EURUSD=X": "Euro / US-Dollar", "EURCHF=X": "Euro / Schweizer Franken", "EURGBP=X": "Euro / Pfund",
    "EURJPY=X": "Euro / Yen", "GBPUSD=X": "Pfund / US-Dollar", "USDJPY=X": "US-Dollar / Yen",
    "^GSPC": "S&P 500", "^VIX": "Volatilitätsindex VIX", "^GDAXI": "DAX", "^NDX": "Nasdaq-100", "^ATX": "ATX",
}

# Standard-Auswahl: große, liquide Werte aus allen Anlageklassen (inkl. Wiener Börse)
DEFAULT_UNIVERSE = {
    "stocks": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM",
               "SAP.DE", "SIE.DE", "ALV.DE", "ASML.AS", "MC.PA", "OMV.VI", "EBS.VI", "VER.VI", "NESN.SW"],
    "etfs": ["SPY", "QQQ", "IWDA.AS", "EXS1.DE", "VWCE.DE", "EEM"],
    "commodities": ["GC=F", "SI=F", "CL=F", "NG=F", "HG=F"],
    "forex": ["EURUSD=X", "EURCHF=X", "EURGBP=X", "EURJPY=X"],
}

SUFFIX_CURRENCY = {
    ".DE": "EUR", ".F": "EUR", ".AS": "EUR", ".PA": "EUR", ".MI": "EUR", ".MC": "EUR", ".VI": "EUR",
    ".BR": "EUR", ".HE": "EUR", ".LS": "EUR", ".IR": "EUR", ".SW": "CHF", ".CO": "DKK", ".ST": "SEK",
    ".OL": "NOK", ".TO": "CAD", ".AX": "AUD", ".T": "JPY", ".HK": "HKD",
}
YAHOO_TICKER_RE = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=]{0,14}$")


@dataclass(frozen=True)
class Instrument:
    symbol: str          # interne Kennung = Ticker, z. B. "BTC/EUR", "AAPL", "GC=F", "EURUSD=X"
    asset_class: str     # crypto | stocks | etfs | commodities | forex
    source: str          # "exchange" (ccxt) oder "yahoo"
    currency: str        # Währung, in der der Kurs notiert
    always_open: bool    # rund um die Uhr handelbar?

    @property
    def name(self) -> str:
        return NAMES.get(self.symbol, self.symbol)

    @property
    def class_label(self) -> str:
        return ASSET_CLASSES.get(self.asset_class, self.asset_class)


def yahoo_currency(ticker: str) -> str:
    """Währung aus dem Yahoo-Ticker ableiten (ohne Netzwerkabfrage)."""
    if ticker.endswith("=X") and len(ticker) == 8:   # EURUSD=X -> Kurs in USD
        return ticker[3:6]
    if ticker.endswith(".L"):
        raise ValueError(f"{ticker}: Londoner Werte notieren in Pence (GBp) und werden nicht unterstützt")
    for suffix, ccy in SUFFIX_CURRENCY.items():
        if ticker.endswith(suffix):
            return ccy
    return "USD"                                     # US-Werte, Futures (=F), Indizes


def valid_yahoo_ticker(ticker: str) -> bool:
    if not YAHOO_TICKER_RE.match(ticker):
        return False
    try:
        yahoo_currency(ticker)
    except ValueError:
        return False
    return True


def build_universe(cfg: dict, include_yahoo: bool = True) -> dict[str, Instrument]:
    """Alle beobachteten Instrumente: Krypto aus `symbols`, der Rest aus `universe`."""
    universe = {}
    for sym in cfg["symbols"]:
        universe[sym] = Instrument(sym, "crypto", "exchange", sym.split("/")[1], True)
    if include_yahoo:
        for asset_class, tickers in (cfg.get("universe") or {}).items():
            if asset_class not in ASSET_CLASSES or asset_class == "crypto":
                continue
            for t in tickers or []:
                universe[t] = Instrument(t, asset_class, "yahoo", yahoo_currency(t),
                                         asset_class == "forex")
    return universe


def fx_ticker(currency: str, base: str = "EUR") -> str | None:
    """Yahoo-Ticker für den Umrechnungskurs base->currency (z. B. EURUSD=X)."""
    return None if currency == base else f"{base}{currency}=X"
