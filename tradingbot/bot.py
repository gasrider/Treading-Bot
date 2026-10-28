"""Der Dauerbetrieb: beobachtet alle Märkte, verwaltet Positionen und Risiko.

Ablauf pro Durchlauf (standardmäßig jede Minute):
 1. Steuerdateien (Not-Aus, Pause, Aufträge) und geänderte Einstellungen übernehmen
 2. Kurse aller Märkte holen (Krypto-Börse + Yahoo), Stops/Ziele offener Positionen prüfen
 3. Risiko-Notbremse, Kapitalverlauf, Gesamtmarktlage
 4. Neue abgeschlossene Kerzen auswerten: Ausstiege sofort, Einstiege gesammelt
 5. Einstiege nach Stärke ordnen, filtern (Ranking, Marktlage, Handelszeiten),
    von der KI prüfen lassen und ausführen
 6. Einmal täglich: KI-Marktanalyse
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import ccxt
import pandas as pd

from . import control
from .ai import AIAnalyst
from .broker import PaperBroker
from .config import config_files, load_config, trading_costs
from .markets import Instrument, build_universe, fx_ticker
from .risk import RiskGuard, position_size
from .scanner import market_regime, momentum_score, rank_percentiles
from .sources import MarketData
from .strategy import add_indicators, initial_levels, signal_at, trail_stop, warmup_bars

log = logging.getLogger(__name__)

EQUITY_HISTORY_MAX = 24 * 365
EVENTS_MAX = 300                          # Ereignis-Protokoll fürs Dashboard
MARKET_CANDLES = 96                       # so viele Kerzen zeigt das Dashboard pro Markt
REGIME_TICKERS = ("^GSPC", "^VIX")
REGIME_REFRESH = 3600


def compact(x: float) -> float:
    """Auf 6 signifikante Stellen runden – hält die State-Datei klein, auch bei Cent-Kursen."""
    return float(f"{x:.6g}")


class TradingBot:
    def __init__(self, cfg, exchange, broker, store, notifier, state=None, config_path=None,
                 market: MarketData | None = None, ai: AIAnalyst | None = None):
        self.cfg = cfg
        self.config_path = config_path
        self._config_stamp = self._stamp()
        self.exchange = exchange
        self.broker = broker
        self.store = store
        self.notifier = notifier
        self.params = cfg["strategy"]
        self.state = state if state is not None else store.load()
        self.state.setdefault("positions", {})
        self.state.setdefault("last_bar", {})
        self.market = market or MarketData(exchange, None)
        self.universe = self._build_universe()
        if isinstance(broker, PaperBroker):
            broker.quote_fn = self._quote
            broker.cost_fn = lambda s: trading_costs(self.cfg, self.inst(s).asset_class)
        self.guard = RiskGuard(cfg["risk"], self.state)
        secrets = cfg.get("secrets", {})
        self.ai = ai or AIAnalyst(cfg.get("ai"), secrets.get("anthropic_api_key", ""), self.state,
                                  gemini_key=secrets.get("gemini_api_key", ""))
        self._halt_notified = self.state["halted"]
        self._regime_checked = 0.0

    # ---------- Märkte ----------
    def _build_universe(self) -> dict[str, Instrument]:
        # Aktien, ETFs, Rohstoffe und Devisen gibt es (vorerst) nur im Paper-Modus
        with_yahoo = self.market.yahoo is not None and self.cfg["mode"] == "paper"
        return build_universe(self.cfg, include_yahoo=with_yahoo)

    def inst(self, symbol: str) -> Instrument:
        """Instrument zu einem Symbol – auch für Positionen, die nicht mehr beobachtet werden."""
        if symbol in self.universe:
            return self.universe[symbol]
        pos = self.state["positions"].get(symbol, {})
        if "/" in symbol:
            return Instrument(symbol, "crypto", "exchange", symbol.split("/")[1], True)
        return Instrument(symbol, pos.get("asset_class", "stocks"), "yahoo", pos.get("currency", "USD"), False)

    def _quote(self, symbol: str) -> tuple[float, float]:
        return self.market.quote(self.inst(symbol))

    # ---------- Hilfsfunktionen ----------
    def equity(self, quotes: dict | None = None) -> float:
        quotes = quotes or {}
        total = self.broker.quote_balance()
        for sym, pos in self.state["positions"].items():
            q = quotes.get(sym)
            if q is None:
                try:
                    q = self._quote(sym)
                except Exception:  # z. B. Yahoo gesperrt: letzten bekannten Kurs nehmen
                    q = (pos.get("last_price", pos["entry"]), pos.get("last_fx", pos.get("fx_entry", 1.0)))
            total += pos["qty"] * q[0] * q[1]
        return total

    def open_position(self, symbol: str, atr_value: float, equity: float, risk_mult: float = 1.0) -> bool:
        inst = self.inst(symbol)
        price, fx = self._quote(symbol)
        stop, _ = initial_levels(price, atr_value, self.params)
        risk_cfg = dict(self.cfg["risk"], risk_per_trade=self.cfg["risk"]["risk_per_trade"] * risk_mult)
        qty = position_size(equity, self.broker.quote_balance(), price * fx, stop * fx, risk_cfg)
        if qty <= 0:
            self.event(f"ℹ️ {symbol}: Kaufsignal, aber Position zu klein bzw. zu wenig Guthaben", notify=False)
            return False
        fill = self.broker.buy(symbol, qty)
        stop, tp = initial_levels(fill.price, atr_value, self.params)
        self.state["positions"][symbol] = {
            "qty": fill.qty, "entry": fill.price, "stop": stop, "tp": tp, "highest": fill.price,
            "cost": fill.qty * fill.price * fill.fx + fill.fee, "fx_entry": fill.fx,
            "last_price": fill.price, "last_fx": fill.fx,
            "currency": inst.currency, "asset_class": inst.asset_class, "name": inst.name,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        self.store.save(self.state)
        ccy = inst.currency
        tp_txt = f"{tp:.4g} {ccy}" if tp else "-"
        mult = f" · Risiko {risk_mult * 100:.0f} %" if risk_mult < 1 else ""
        self.event(
            f"🟢 KAUF {symbol} ({inst.name}): {fill.qty:.6g} @ {fill.price:.4g} {ccy} "
            f"| Stop {stop:.4g} | Ziel {tp_txt}{mult}", "trade"
        )
        return True

    def close_position(self, symbol: str, reason: str) -> None:
        pos = self.state["positions"][symbol]
        fill = self.broker.sell(symbol, pos["qty"])
        proceeds = fill.qty * fill.price * fill.fx - fill.fee
        pnl = proceeds - pos["cost"] * (fill.qty / pos["qty"])
        del self.state["positions"][symbol]
        self.state.setdefault("closed_trades", []).append({
            "symbol": symbol, "entry": pos["entry"], "exit": fill.price, "qty": fill.qty,
            "pnl": round(pnl, 4), "reason": reason, "opened_at": pos["opened_at"],
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "currency": pos.get("currency", self.cfg["quote"]), "asset_class": pos.get("asset_class", "crypto"),
        })
        self.store.save(self.state)
        icon = "✅" if pnl > 0 else "🔴"
        self.event(
            f"{icon} VERKAUF {symbol} ({reason}): {fill.qty:.6g} @ {fill.price:.4g} {pos.get('currency', '')} "
            f"| Ergebnis {pnl:+.2f} {self.cfg['quote']}", "trade"
        )

    def close_all(self, reason: str) -> None:
        for symbol in list(self.state["positions"]):
            self.close_position(symbol, reason)

    def event(self, text: str, kind: str = "info", notify: bool = True) -> None:
        """Ereignis fürs Dashboard-Protokoll merken und (optional) per Telegram melden."""
        events = self.state.setdefault("events", [])
        events.append({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, "text": text})
        del events[:-EVENTS_MAX]
        if notify:
            self.notifier.send(text)
        else:
            log.info(text)

    def record_equity(self, equity: float) -> None:
        """Kapitalverlauf höchstens einmal pro Stunde mitschreiben (für das Dashboard)."""
        history = self.state.setdefault("equity_history", [])
        now = datetime.now(timezone.utc)
        if history and now - datetime.fromisoformat(history[-1][0]) < timedelta(hours=1):
            return
        history.append([now.isoformat(timespec="seconds"), round(equity, 2)])
        del history[:-EQUITY_HISTORY_MAX]

    def handle_control_files(self) -> None:
        if control.RESUME_FILE.exists():
            control.KILL_SWITCH.unlink(missing_ok=True)
            control.RESUME_FILE.unlink(missing_ok=True)
            if self.state["halted"]:
                self.state["halted"] = False
                self.state["halt_reason"] = ""
                self.state.pop("peak_equity", None)
                self._halt_notified = False
                self.event("▶️ Handel wieder freigegeben.")
        paused = control.PAUSE_FILE.exists()
        if paused != self.state.get("paused", False):
            self.state["paused"] = paused
            self.event("⏸️ Pause: keine neuen Käufe, Stops bleiben aktiv." if paused
                       else "▶️ Pause beendet: der Bot kauft wieder bei Signalen.")
        for cmd in control.take_commands():
            symbol = cmd.get("symbol")
            if cmd.get("action") == "close" and symbol in self.state["positions"]:
                self.state["positions"][symbol]["pending_exit"] = "Manuell geschlossen"
            else:
                log.warning("Unbekannter oder veralteter Auftrag ignoriert: %s", cmd)
        if control.KILL_SWITCH.exists() and not (
            self.state["halted"] and self.state["halt_reason"].startswith("Not-Aus")
        ):
            self.close_all("Not-Aus")
            self.state["halted"] = True
            self.state["halt_reason"] = "Not-Aus ausgelöst"

    def _stamp(self):
        if not self.config_path:
            return None
        return tuple(p.stat().st_mtime if p.exists() else None for p in config_files(self.config_path))

    def reload_config_if_changed(self) -> None:
        """Änderungen aus Dashboard/config.yaml ohne Neustart übernehmen."""
        stamp = self._stamp()
        if stamp == self._config_stamp:
            return
        self._config_stamp = stamp
        try:
            new = load_config(self.config_path)
        except Exception as e:
            log.error("Neue Einstellungen ungültig, alte bleiben aktiv: %s", e)
            self.event(f"⚠️ Einstellungen nicht übernommen: {e}", "warn")
            return
        if new["mode"] != self.cfg["mode"] or new["exchange"] != self.cfg["exchange"]:
            log.warning("Modus/Börse ändern sich erst nach einem Neustart (tradingbot restart).")
            new["mode"], new["exchange"] = self.cfg["mode"], self.cfg["exchange"]
        self.cfg = new
        self.params = new["strategy"]
        self.guard.cfg = new["risk"]
        self.ai.cfg.update(new.get("ai") or {})
        self.universe = self._build_universe()
        self.event(
            f"⚙️ Neue Einstellungen aktiv: {len(self.universe)} Märkte auf {new['timeframe']}, "
            f"Risiko {new['risk']['risk_per_trade'] * 100:g} % pro Trade, KI: {self.ai.mode}", "settings"
        )

    # ---------- Marktlage & Momentaufnahmen ----------
    def update_regime(self) -> dict:
        cfg = self.cfg.get("regime") or {}
        if not cfg.get("enabled", True) or self.market.yahoo is None:
            regime = {"state": "unknown", "multiplier": 1.0, "label": "Aus",
                      "text": "Marktlage-Filter ist ausgeschaltet."}
        elif time.time() - self._regime_checked < REGIME_REFRESH and "regime" in self.state:
            return self.state["regime"]
        else:
            y = self.market.yahoo
            y.refresh(list(REGIME_TICKERS), "1d")
            regime = market_regime(y.all_candles(REGIME_TICKERS[0], "1d"), y.all_candles(REGIME_TICKERS[1], "1d"), cfg)
            self._regime_checked = time.time()
        regime["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        old = (self.state.get("regime") or {}).get("state")
        if old and old != regime["state"] and regime["state"] != "unknown":
            self.event(f"🌍 Marktlage: {regime['label']} – {regime['text']}", "warn" if regime["multiplier"] < 1 else "info")
        self.state["regime"] = regime
        return regime

    def update_market(self, symbol: str, data: pd.DataFrame, price: float | None, is_open: bool) -> dict:
        """Momentaufnahme dessen, was der Bot sieht – für Dashboard, Scanner und KI."""
        inst = self.inst(symbol)
        tail = data.iloc[-MARKET_CANDLES:]
        last = data.iloc[-1]
        snap = {
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "timeframe": self.cfg["timeframe"], "name": inst.name, "asset_class": inst.asset_class,
            "currency": inst.currency, "source": inst.source, "open": is_open,
            "price": compact(price if price else last["close"]),
            "candles": [
                [ts.isoformat(), *(compact(row[k]) for k in ("open", "high", "low", "close"))]
                for ts, row in tail.iterrows()
            ],
            "ema_fast": [compact(v) for v in tail["ema_fast"]],
            "ema_slow": [compact(v) for v in tail["ema_slow"]],
            "ema_trend": [compact(v) for v in tail["ema_trend"]],
            "rsi": round(float(last["rsi"]), 1),
            "atr": compact(last["atr"]),
            "score": momentum_score(data),
            "uptrend": bool(last["close"] > last["ema_trend"]),
            "fast_above_slow": bool(last["ema_fast"] > last["ema_slow"]),
            "rsi_ok": bool(last["rsi"] < self.params["rsi_max_entry"]),
            "entry_signal": bool(last["entry"]),
            "exit_signal": bool(last["exit"]),
            "error": "",
        }
        self.state.setdefault("market", {})[symbol] = snap
        return snap

    def entry_context(self, symbol: str, data: pd.DataFrame, price: float, stop: float, tp: float | None,
                      equity: float, regime: dict) -> dict:
        """Alles, was die KI für die Prüfung eines Kaufsignals braucht."""
        inst, last = self.inst(symbol), data.iloc[-1]
        snap = self.state["market"][symbol]
        closes = data["close"].iloc[-30:]
        positions = [
            {"symbol": s, "klasse": p.get("asset_class", ""), "name": p.get("name", s),
             "ergebnis_pct": round((p.get("last_price", p["entry"]) / p["entry"] - 1) * 100, 2)}
            for s, p in self.state["positions"].items()
        ]
        return {
            "zeit_utc": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "instrument": {"symbol": symbol, "name": inst.name, "klasse": inst.class_label, "waehrung": inst.currency},
            "kerzen_intervall": self.cfg["timeframe"],
            "signal": {
                "kurs": compact(price), "stop": compact(stop), "ziel": compact(tp) if tp else None,
                "ema_schnell": compact(last["ema_fast"]), "ema_langsam": compact(last["ema_slow"]),
                "ema_trend": compact(last["ema_trend"]), "rsi": round(float(last["rsi"]), 1),
                "abstand_zur_trendlinie_pct": round(float(last["close"] / last["ema_trend"] - 1) * 100, 2),
                "atr_pct": round(float(last["atr"] / last["close"]) * 100, 2),
                "staerke_bewertung": snap.get("score"), "ranking_perzentil": snap.get("rank"),
            },
            "letzte_30_schlusskurse": [compact(v) for v in closes],
            "marktlage": {k: regime.get(k) for k in ("label", "text", "multiplier")},
            "portfolio": {"kapital_eur": round(equity, 2), "offene_positionen": positions,
                          "max_positionen": self.cfg["risk"]["max_open_positions"]},
        }

    def market_overview(self) -> dict:
        """Kompakte Übersicht aller Märkte für die tägliche KI-Analyse."""
        rows = []
        for sym, m in sorted(self.state.get("market", {}).items(), key=lambda kv: -(kv[1].get("score") or -99)):
            if not m.get("candles"):
                continue
            rows.append({"symbol": sym, "name": m.get("name", sym), "klasse": m.get("asset_class"),
                         "kurs": m["price"], "waehrung": m.get("currency"),
                         "veraenderung_pct": round((m["candles"][-1][4] / m["candles"][0][1] - 1) * 100, 2),
                         "rsi": m.get("rsi"), "aufwaertstrend": m.get("uptrend"), "staerke": m.get("score")})
        return {"datum": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "kerzen": self.cfg["timeframe"],
                "marktlage": self.state.get("regime"), "maerkte": rows,
                "offene_positionen": list(self.state["positions"])}

    # ---------- Ein Durchlauf ----------
    def step(self) -> None:
        self.reload_config_if_changed()
        self.handle_control_files()
        if self.state["halted"] and self.state["halt_reason"].startswith("Not-Aus"):
            if not self._halt_notified:
                self.event("🛑 Not-Aus: alle Positionen verkauft, Bot handelt nicht mehr.", "warn")
                self._halt_notified = True
            self.store.save(self.state)
            return

        held = list(self.state["positions"])
        symbols = list(dict.fromkeys([*self.universe, *held]))
        for symbol in list(self.state.get("market", {})):
            if symbol not in symbols:
                del self.state["market"][symbol]
        self.market.prepare({s: self.inst(s) for s in symbols}, self.cfg["timeframe"], held)

        # 1) Aktuelle Kurse holen; Stops, Ziele und vorgemerkte Verkäufe prüfen
        quotes, open_now = {}, {}
        for symbol in symbols:
            inst = self.inst(symbol)
            try:
                quotes[symbol] = self.market.quote(inst)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable):
                if symbol in self.state["positions"] and inst.source == "exchange":
                    raise  # ohne Kurs keine Stop-Überwachung – ganzer Durchlauf neu
            except Exception as e:
                log.debug("%s: Kurs nicht abrufbar: %s", symbol, e)
            open_now[symbol] = self.market.is_open(inst)
        for symbol, pos in list(self.state["positions"].items()):
            if symbol not in quotes:
                continue
            price, fx = quotes[symbol]
            pos["last_price"], pos["last_fx"] = price, fx
            if not open_now.get(symbol):
                continue  # Markt geschlossen: Stops greifen erst bei Öffnung (dann ggf. mit Kurslücke)
            if pos.get("pending_exit"):
                self.close_position(symbol, pos["pending_exit"])
            elif price <= pos["stop"]:
                self.close_position(symbol, "Stop-Loss")
            elif pos["tp"] and price >= pos["tp"]:
                self.close_position(symbol, "Take-Profit")

        # 2) Risiko-Notbremse, Kapitalverlauf, Vergleichswert, KI-Bilanz
        equity = self.equity(quotes)
        self.state["equity"] = round(equity, 2)
        if "benchmark" not in self.state and quotes:
            # Vergleichswert: was "einfach kaufen und halten" seit dem Start gebracht hätte
            self.state["benchmark"] = {
                "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "equity": round(equity, 2),
                "prices": {s: q[0] for s, q in quotes.items() if s in self.universe},
            }
        self.guard.update(equity)
        self.record_equity(equity)
        self.ai.update_shadows({s: q[0] for s, q in quotes.items()})
        if self.state["halted"]:
            if self.state["positions"]:
                self.close_all("Notbremse")
            if not self._halt_notified:
                self.event("⛔ " + self.state["halt_reason"] + " – Bot handelt nicht mehr.", "warn")
                self._halt_notified = True
            self.store.save(self.state)
            return

        # 3) Gesamtmarktlage
        regime = self.update_regime()

        # 4) Kerzen auswerten – ein fehlerhafter Markt blockiert nicht die anderen
        candidates = []
        for symbol in symbols:
            try:
                found = self.process_symbol(symbol, quotes.get(symbol), open_now.get(symbol, False))
                if found:
                    candidates.append(found)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable):
                raise
            except Exception as e:
                log.error("%s: %s", symbol, e)
                self.state.setdefault("market", {}).setdefault(symbol, {})["error"] = str(e)[:200]
        ranks = rank_percentiles({s: m.get("score") for s, m in self.state.get("market", {}).items()})
        for symbol, m in self.state.get("market", {}).items():
            m["rank"] = ranks.get(symbol)

        # 5) Einstiege: stärkste zuerst, dann filtern, KI prüfen, kaufen
        candidates.sort(key=lambda c: -(self.state["market"][c[0]].get("score") or -99))
        for symbol, sig, data in candidates:
            self.try_entry(symbol, sig, data, regime, open_now.get(symbol, False))

        # 6) Tägliche KI-Marktanalyse
        if self.ai.brief_due():
            brief = self.ai.market_brief(self.market_overview())
            if brief:
                self.event("🧠 Neue KI-Marktanalyse verfügbar (Dashboard → KI).", "info", notify=False)
        self.state["ai"]["status"] = {"mode": self.ai.mode, "available": self.ai.available, "model": self.ai.model_name,
                                      "provider": self.ai.provider, "budget_usd": self.ai.cfg["monthly_budget_usd"]}
        self.state["data_error"] = getattr(self.market.yahoo, "last_error", "") if self.market.yahoo else ""
        self.store.save(self.state)

    def process_symbol(self, symbol: str, quote: tuple | None, is_open: bool):
        """Kerzen eines Marktes auswerten. Liefert (symbol, signal, daten) bei neuem Kaufsignal."""
        inst = self.inst(symbol)
        limit = min(720, warmup_bars(self.params) + 300)
        df = self.market.candles(inst, self.cfg["timeframe"], limit)
        if len(df) < warmup_bars(self.params):
            self.state.setdefault("market", {}).setdefault(symbol, {})["error"] = (
                f"zu wenige Kerzen ({len(df)}) – Daten werden geladen" if len(df) else "noch keine Kursdaten")
            return None
        data = add_indicators(df, self.params)
        self.update_market(symbol, data, quote[0] if quote else None, is_open)
        last_ts = str(df.index[-1])
        if self.state["last_bar"].get(symbol) == last_ts:
            return None
        self.state["last_bar"][symbol] = last_ts
        sig = signal_at(data, -1)
        pos = self.state["positions"].get(symbol)
        log.info("%s Kerze %s: Schluss %.4g | EMA %.4g/%.4g/%.4g | RSI %.1f", symbol, last_ts, sig.close,
                 data["ema_fast"].iat[-1], data["ema_slow"].iat[-1], data["ema_trend"].iat[-1], data["rsi"].iat[-1])
        if pos:
            pos["highest"] = max(pos["highest"], sig.high)
            pos["stop"] = trail_stop(pos["stop"], pos["highest"], sig.atr, self.params)
            if sig.exit:
                if is_open:
                    self.close_position(symbol, "Trendwende")
                else:
                    pos["pending_exit"] = "Trendwende"
            return None
        if sig.entry and symbol in self.universe:
            return symbol, sig, data
        return None

    def try_entry(self, symbol: str, sig, data: pd.DataFrame, regime: dict, is_open: bool) -> None:
        snap = self.state["market"][symbol]
        scanner = self.cfg.get("scanner") or {}
        top_pct = scanner.get("top_pct", 1.0)
        apply_regime = (self.cfg.get("regime") or {}).get("apply_to") or list(
            {"crypto", "stocks", "etfs", "commodities", "forex"})
        mult = regime.get("multiplier", 1.0) if self.inst(symbol).asset_class in apply_regime else 1.0
        inst = self.inst(symbol)
        if inst.source == "yahoo" and self.market.yahoo:
            # Vor einem Kauf: aktuellen 5-Minuten-Kurs holen – zeigt verlässlich, ob gehandelt wird
            held = [s for s in self.state["positions"] if self.inst(s).source == "yahoo"]
            fx = [t for t in {fx_ticker(self.inst(s).currency, self.cfg["quote"]) for s in [symbol, *held]} if t]
            self.market.yahoo.refresh_quotes([symbol, *held, *fx])
            is_open = self.market.is_open(inst)
        equity = self.equity()
        ok, why = self.guard.can_open(equity, len(self.state["positions"]))
        if ok and self.state.get("paused"):
            ok, why = False, "Bot ist pausiert"
        if ok and not is_open:
            ok, why = False, "Markt gerade geschlossen"
        if ok and snap.get("rank") is not None and snap["rank"] < 1 - top_pct:
            ok, why = False, f"nicht unter den stärksten {top_pct * 100:.0f} % (Platz-Perzentil {snap['rank']:.2f})"
        if ok and mult <= 0:
            ok, why = False, f"Marktlage „{regime.get('label')}“ – keine neuen Käufe"
        if not ok:
            self.event(f"ℹ️ {symbol}: Kaufsignal ignoriert – {why}", "info", notify=False)
            return
        if self.ai.available:
            price = self._quote(symbol)[0]
            stop, tp = initial_levels(price, sig.atr, self.params)
            review = self.ai.review_entry(self.entry_context(symbol, data, price, stop, tp, equity, regime))
            enforced = self.ai.mode == "veto"
            self.ai.record(symbol, review, enforced, {"price": price, "stop": stop, "tp": tp})
            if review.decision == "reject":
                verb = "verhindert" if enforced else "hätte abgelehnt (nur Hinweis)"
                self.event(f"🤖 KI {verb}: Kauf {symbol} – {review.reason}", "ai")
                if enforced:
                    return
            elif review.decision == "error":
                self.event(f"⚠️ KI-Prüfung {symbol} nicht möglich ({review.reason}) – "
                           f"{'Regeln entscheiden' if review.approve else 'kein Kauf'}", "warn", notify=False)
                if not review.approve:
                    return
            else:
                self.event(f"🤖 KI genehmigt Kauf {symbol}: {review.reason}", "ai", notify=False)
        self.open_position(symbol, sig.atr, equity, mult)

    def run(self) -> None:
        mode = self.cfg["mode"].upper()
        try:
            capital = f"Kapital {self.equity():.2f} {self.cfg['quote']}"
        except Exception as e:  # Börse beim Start nicht erreichbar – trotzdem weiterlaufen
            capital = f"Kapital noch unbekannt ({e.__class__.__name__})"
        classes = sorted({i.class_label for i in self.universe.values()})
        self.event(
            f"🤖 Bot gestartet ({mode}) – {len(self.universe)} Märkte ({', '.join(classes)}) auf "
            f"{self.cfg['timeframe']}, KI: {self.ai.mode} ({self.ai.provider if self.ai.available else 'kein Schlüssel'}), " + capital
        )
        if self.cfg["mode"] == "live" and (self.cfg.get("universe") or {}):
            self.event("ℹ️ Aktien, ETFs, Rohstoffe und Devisen laufen nur im Paper-Modus – live wird nur Krypto gehandelt.",
                       notify=False)
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
                self.event("⏹️ Bot-Prozess beendet.", "warn")
                raise
            except Exception as e:  # Bot soll bei unerwarteten Fehlern nicht sterben
                errors += 1
                self.state["last_error"] = str(e)[:300]
                log.exception("Fehler im Durchlauf")
                if errors in (1, 5, 20):
                    self.event(f"⚠️ Fehler im Bot ({errors}x): {e}", "error")
            self.state["heartbeat"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.state["mode"] = self.cfg["mode"]
            self.store.save(self.state)
            time.sleep(self.cfg["loop"]["poll_seconds"] * min(2 ** max(errors - 1, 0), 10))


def status_text(state: dict, quote: str) -> str:
    lines = [f"Gestoppt: {'JA – ' + state.get('halt_reason', '') if state.get('halted') else 'nein'}"]
    if "paper_cash" in state:
        lines.append(f"Paper-Guthaben: {state['paper_cash']:.2f} {quote}")
    lines.append(f"Höchststand Kapital: {state.get('peak_equity', 0):.2f} {quote}")
    if state.get("regime"):
        lines.append(f"Marktlage: {state['regime'].get('label')} – {state['regime'].get('text')}")
    positions = state.get("positions", {})
    lines.append(f"Offene Positionen: {len(positions)}")
    for sym, p in positions.items():
        lines.append(f"  {sym}: {p['qty']:.6g} @ {p['entry']:.4g} {p.get('currency', '')} | Stop {p['stop']:.4g}")
    trades = state.get("closed_trades", [])
    if trades:
        df = pd.DataFrame(trades)
        wins = (df["pnl"] > 0).sum()
        lines.append(
            f"Abgeschlossene Trades: {len(df)} | Gewinner {wins} | Summe {df['pnl'].sum():+.2f} {quote}"
        )
    return "\n".join(lines)
