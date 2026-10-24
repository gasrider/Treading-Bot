"""Ausführung von Orders: PaperBroker (simuliert) und LiveBroker (echte Börse via ccxt)."""
import logging
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Fill:
    qty: float      # tatsächlich gehaltene/verkaufte Menge
    price: float    # Durchschnittspreis in der Notierungswährung des Instruments
    fee: float      # Gebühr in Konto-Währung (EUR)
    fx: float = 1.0  # Umrechnungsfaktor Notierungswährung -> Konto-Währung


class PaperBroker:
    """Echte Kurse, virtuelles Geld. Kontostand (EUR) liegt im state-Dict.

    Kurse kommen über `quote_fn(symbol) -> (kurs, fx)`; der Bot setzt dafür seine
    Marktdaten ein (Krypto, Aktien, Rohstoffe, Devisen). Werte in Fremdwährung werden
    mit dem aktuellen Wechselkurs in EUR umgerechnet.
    """

    def __init__(self, source, state: dict, starting_balance: float, fee: float, slippage: float):
        self.quote_fn = None
        if source is not None and hasattr(source, "fetch_ticker"):
            self.quote_fn = lambda s: (float(source.fetch_ticker(s)["last"]), 1.0)
        self.state = state
        self.fee = fee
        self.slippage = slippage
        self.cost_fn = lambda symbol: (self.fee, self.slippage)  # der Bot setzt Kosten je Anlageklasse
        state.setdefault("paper_cash", float(starting_balance))

    def quote(self, symbol: str) -> tuple[float, float]:
        return self.quote_fn(symbol)

    def price(self, symbol: str) -> float:
        return self.quote(symbol)[0]

    def quote_balance(self) -> float:
        return self.state["paper_cash"]

    def buy(self, symbol: str, qty: float) -> Fill:
        fee_rate, slippage = self.cost_fn(symbol)
        price, fx = self.quote(symbol)
        price *= 1 + slippage
        cost = qty * price * fx
        fee = cost * fee_rate
        if cost + fee > self.state["paper_cash"]:
            qty = self.state["paper_cash"] / (price * fx * (1 + fee_rate))
            cost = qty * price * fx
            fee = cost * fee_rate
        self.state["paper_cash"] -= cost + fee
        return Fill(qty, price, fee, fx)

    def sell(self, symbol: str, qty: float) -> Fill:
        fee_rate, slippage = self.cost_fn(symbol)
        price, fx = self.quote(symbol)
        price *= 1 - slippage
        proceeds = qty * price * fx
        fee = proceeds * fee_rate
        self.state["paper_cash"] += proceeds - fee
        return Fill(qty, price, fee, fx)


class LiveBroker:
    """Echte Market-Orders, nur Spot und nur Long (kein Hebel, keine Leerverkäufe)."""

    def __init__(self, exchange, quote: str):
        self.exchange = exchange
        self.quote_ccy = quote
        exchange.load_markets()
        if not exchange.has.get("fetchBalance"):
            raise RuntimeError(f"{exchange.id} unterstützt kein fetchBalance")

    def price(self, symbol: str) -> float:
        return float(self.exchange.fetch_ticker(symbol)["last"])

    def quote(self, symbol: str) -> tuple[float, float]:
        return self.price(symbol), 1.0  # Krypto-Paare notieren in der Konto-Währung

    def quote_balance(self) -> float:
        return float(self.exchange.fetch_balance()["free"].get(self.quote_ccy, 0.0) or 0.0)

    def base_balance(self, symbol: str) -> float:
        base = symbol.split("/")[0]
        return float(self.exchange.fetch_balance()["free"].get(base, 0.0) or 0.0)

    def _check_limits(self, symbol: str, qty: float, price: float) -> float:
        market = self.exchange.market(symbol)
        qty = float(self.exchange.amount_to_precision(symbol, qty))
        min_amount = (market.get("limits", {}).get("amount") or {}).get("min") or 0
        min_cost = (market.get("limits", {}).get("cost") or {}).get("min") or 0
        if qty < min_amount or qty * price < min_cost:
            raise ValueError(
                f"Order zu klein für {symbol}: {qty} (min. Menge {min_amount}, min. Wert {min_cost})"
            )
        return qty

    def _execute(self, symbol: str, side: str, qty: float) -> Fill:
        price = self.price(symbol)
        qty = self._check_limits(symbol, qty, price)
        order = self.exchange.create_order(symbol, "market", side, qty)
        for _ in range(10):
            if order.get("status") == "closed" or order.get("filled"):
                break
            time.sleep(1)
            order = self.exchange.fetch_order(order["id"], symbol)
        filled = float(order.get("filled") or qty)
        avg = float(order.get("average") or order.get("price") or price)
        fee_info = order.get("fee") or {}
        fee_cost = float(fee_info.get("cost") or 0.0)
        base = symbol.split("/")[0]
        if fee_info.get("currency") == base:
            if side == "buy":
                filled -= fee_cost
            fee_cost *= avg
        log.info("LIVE %s %s %.8f @ %.4f (Gebühr %.4f %s)", side, symbol, filled, avg, fee_cost, self.quote_ccy)
        return Fill(filled, avg, fee_cost)

    def buy(self, symbol: str, qty: float) -> Fill:
        return self._execute(symbol, "buy", qty)

    def sell(self, symbol: str, qty: float) -> Fill:
        qty = min(qty, self.base_balance(symbol))
        return self._execute(symbol, "sell", qty)
