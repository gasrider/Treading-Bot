"""Der Dauerbetrieb: prüft regelmäßig den Markt, verwaltet Positionen und Risiko."""
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import pandas as pd

from .data import fetch_recent
from .risk import RiskGuard, position_size
from .strategy import add_indicators, initial_levels, signal_at, trail_stop, warmup_bars

log = logging.getLogger(__name__)

KILL_SWITCH = Path("state") / "STOP"      # anlegen = alles verkaufen, Handel stoppen
RESUME_FILE = Path("state") / "RESUME"    # anlegen = Stopp/Notbremse aufheben
EQUITY_HISTORY_MAX = 24 * 365


class TradingBot:
    def __init__(self, cfg, exchange, broker, store, notifier, state=None):
        self.cfg = cfg
        self.exchange = exchange
        self.broker = broker
        self.store = store
        self.notifier = notifier
        self.params = cfg["strategy"]
        self.state = state if state is not None else store.load()
        self.state.setdefault("positions", {})
        self.state.setdefault("last_bar", {})
        self.guard = RiskGuard(cfg["risk"], self.state)
        self._halt_notified = self.state["halted"]

    # ---------- Hilfsfunktionen ----------
    def equity(self, prices: dict | None = None) -> float:
        prices = prices or {}
        total = self.broker.quote_balance()
        for sym, pos in self.state["positions"].items():
            total += pos["qty"] * (prices.get(sym) or self.broker.price(sym))
        return total

    def open_position(self, symbol: str, atr_value: float, equity: float) -> None:
        price = self.broker.price(symbol)
        stop, _ = initial_levels(price, atr_value, self.params)
        qty = position_size(equity, self.broker.quote_balance(), price, stop, self.cfg["risk"])
        if qty <= 0:
            log.info("%s: Kaufsignal, aber Position zu klein / zu wenig Guthaben", symbol)
            return
        fill = self.broker.buy(symbol, qty)
        stop, tp = initial_levels(fill.price, atr_value, self.params)
        self.state["positions"][symbol] = {
            "qty": fill.qty, "entry": fill.price, "stop": stop, "tp": tp,
            "highest": fill.price, "cost": fill.qty * fill.price + fill.fee,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        self.store.save(self.state)
        tp_txt = f"{tp:.2f}" if tp else "-"
        self.notifier.send(
            f"🟢 KAUF {symbol}: {fill.qty:.6f} @ {fill.price:.2f} {self.cfg['quote']} "
            f"| Stop {stop:.2f} | Ziel {tp_txt}"
        )

    def close_position(self, symbol: str, reason: str) -> None:
        pos = self.state["positions"][symbol]
        fill = self.broker.sell(symbol, pos["qty"])
        pnl = fill.qty * fill.price - fill.fee - pos["cost"] * (fill.qty / pos["qty"])
        del self.state["positions"][symbol]
        self.state.setdefault("closed_trades", []).append({
            "symbol": symbol, "entry": pos["entry"], "exit": fill.price, "qty": fill.qty,
            "pnl": round(pnl, 4), "reason": reason, "opened_at": pos["opened_at"],
            "closed_at": datetime.now(timezone.utc).isoformat(),
        })
        self.store.save(self.state)
        icon = "✅" if pnl > 0 else "🔴"
        self.notifier.send(
            f"{icon} VERKAUF {symbol} ({reason}): {fill.qty:.6f} @ {fill.price:.2f} "
            f"| Ergebnis {pnl:+.2f} {self.cfg['quote']}"
        )

    def close_all(self, reason: str) -> None:
        for symbol in list(self.state["positions"]):
            self.close_position(symbol, reason)

    def record_equity(self, equity: float) -> None:
        """Kapitalverlauf höchstens einmal pro Stunde mitschreiben (für das Dashboard)."""
        history = self.state.setdefault("equity_history", [])
        now = datetime.now(timezone.utc)
        if history and now - datetime.fromisoformat(history[-1][0]) < timedelta(hours=1):
            return
        history.append([now.isoformat(timespec="seconds"), round(equity, 2)])
        del history[:-EQUITY_HISTORY_MAX]

    def handle_control_files(self) -> None:
        if RESUME_FILE.exists():
            KILL_SWITCH.unlink(missing_ok=True)
            RESUME_FILE.unlink(missing_ok=True)
            if self.state["halted"]:
                self.state["halted"] = False
                self.state["halt_reason"] = ""
                self.state.pop("peak_equity", None)
                self._halt_notified = False
                self.notifier.send("▶️ Handel wieder freigegeben.")
        if KILL_SWITCH.exists() and not (
            self.state["halted"] and self.state["halt_reason"].startswith("Not-Aus")
        ):
            self.close_all("Not-Aus")
            self.state["halted"] = True
            self.state["halt_reason"] = "Not-Aus ausgelöst"

    # ---------- Ein Durchlauf ----------
    def step(self) -> None:
        self.handle_control_files()
        if self.state["halted"] and self.state["halt_reason"].startswith("Not-Aus"):
            if not self._halt_notified:
                self.notifier.send("🛑 Not-Aus: alle Positionen verkauft, Bot handelt nicht mehr.")
                self._halt_notified = True
            self.store.save(self.state)
            return

        # 1) Stops und Take-Profits mit aktuellem Kurs prüfen
        prices = {}
        for symbol, pos in list(self.state["positions"].items()):
            price = self.broker.price(symbol)
            prices[symbol] = price
            if price <= pos["stop"]:
                self.close_position(symbol, "Stop-Loss")
            elif pos["tp"] and price >= pos["tp"]:
                self.close_position(symbol, "Take-Profit")

        # 2) Risiko-Notbremse
        equity = self.equity(prices)
        self.guard.update(equity)
        self.record_equity(equity)
        if self.state["halted"]:
            if self.state["positions"]:
                self.close_all("Notbremse")
            if not self._halt_notified:
                self.notifier.send("⛔ " + self.state["halt_reason"] + " – Bot handelt nicht mehr.")
                self._halt_notified = True
            self.store.save(self.state)
            return

        # 3) Neue abgeschlossene Kerzen auswerten
        limit = min(720, warmup_bars(self.params) + 300)
        for symbol in self.cfg["symbols"]:
            df = fetch_recent(self.exchange, symbol, self.cfg["timeframe"], limit=limit)
            if len(df) < warmup_bars(self.params):
                log.warning("%s: zu wenige Kerzen (%d)", symbol, len(df))
                continue
            last_ts = str(df.index[-1])
            if self.state["last_bar"].get(symbol) == last_ts:
                continue
            self.state["last_bar"][symbol] = last_ts
            data = add_indicators(df, self.params)
            sig = signal_at(data, -1)
            pos = self.state["positions"].get(symbol)
            log.info(
                "%s Kerze %s: Schluss %.2f | EMA %.2f/%.2f/%.2f | RSI %.1f",
                symbol, last_ts, sig.close, data["ema_fast"].iat[-1], data["ema_slow"].iat[-1],
                data["ema_trend"].iat[-1], data["rsi"].iat[-1],
            )
            if pos:
                pos["highest"] = max(pos["highest"], sig.high)
                pos["stop"] = trail_stop(pos["stop"], pos["highest"], sig.atr, self.params)
                if sig.exit:
                    self.close_position(symbol, "Trendwende")
            elif sig.entry:
                ok, why = self.guard.can_open(self.equity(), len(self.state["positions"]))
                if ok:
                    self.open_position(symbol, sig.atr, self.equity())
                else:
                    log.info("%s: Kaufsignal ignoriert – %s", symbol, why)
        self.store.save(self.state)

    def run(self) -> None:
        mode = self.cfg["mode"].upper()
        try:
            capital = f"Kapital {self.equity():.2f} {self.cfg['quote']}"
        except Exception as e:  # Börse beim Start nicht erreichbar – trotzdem weiterlaufen
            capital = f"Kapital noch unbekannt ({e.__class__.__name__})"
        self.notifier.send(
            f"🤖 Bot gestartet ({mode}) – {', '.join(self.cfg['symbols'])} auf {self.cfg['timeframe']}, "
            + capital
        )
        errors = 0
        while True:
            try:
                self.step()
                errors = 0
                self.state["last_error"] = ""
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
                errors += 1
                self.state["last_error"] = f"Netzwerk: {e}"[:300]
                log.warning("Netzwerkproblem (%d): %s", errors, e)
            except (SystemExit, KeyboardInterrupt):
                self.store.save(self.state)
                self.notifier.send("⏹️ Bot-Prozess beendet.")
                raise
            except Exception as e:  # Bot soll bei unerwarteten Fehlern nicht sterben
                errors += 1
                self.state["last_error"] = str(e)[:300]
                log.exception("Fehler im Durchlauf")
                if errors in (1, 5, 20):
                    self.notifier.send(f"⚠️ Fehler im Bot ({errors}x): {e}")
            self.state["heartbeat"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.state["mode"] = self.cfg["mode"]
            self.store.save(self.state)
            time.sleep(self.cfg["loop"]["poll_seconds"] * min(2 ** max(errors - 1, 0), 10))


def status_text(state: dict, quote: str) -> str:
    lines = [f"Gestoppt: {'JA – ' + state.get('halt_reason', '') if state.get('halted') else 'nein'}"]
    if "paper_cash" in state:
        lines.append(f"Paper-Guthaben: {state['paper_cash']:.2f} {quote}")
    lines.append(f"Höchststand Kapital: {state.get('peak_equity', 0):.2f} {quote}")
    positions = state.get("positions", {})
    lines.append(f"Offene Positionen: {len(positions)}")
    for sym, p in positions.items():
        lines.append(f"  {sym}: {p['qty']:.6f} @ {p['entry']:.2f} | Stop {p['stop']:.2f}")
    trades = state.get("closed_trades", [])
    if trades:
        df = pd.DataFrame(trades)
        wins = (df["pnl"] > 0).sum()
        lines.append(
            f"Abgeschlossene Trades: {len(df)} | Gewinner {wins} | Summe {df['pnl'].sum():+.2f} {quote}"
        )
    return "\n".join(lines)
