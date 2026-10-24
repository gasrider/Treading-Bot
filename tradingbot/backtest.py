"""Backtest: dieselbe Strategie- und Risikologik wie im Live-Betrieb, auf historischen Daten.

Signale entstehen beim Schluss einer Kerze und werden zum Eröffnungskurs der nächsten
Kerze ausgeführt (kein Blick in die Zukunft). Stops/Take-Profits werden innerhalb der
Kerze über Hoch/Tief geprüft; bei Kurslücken wird zum (schlechteren) Eröffnungskurs gefüllt.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .risk import RiskGuard, position_size
from .strategy import add_indicators, initial_levels, trail_stop, warmup_bars


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)


def run_backtest(df: pd.DataFrame, cfg: dict, costs: tuple[float, float] | None = None) -> BacktestResult:
    """costs = (Gebühr, Slippage) pro Order; Standard aus dem Abschnitt backtest."""
    params, risk_cfg, bt = cfg["strategy"], cfg["risk"], cfg["backtest"]
    fee, slip = costs or (bt["fee"], bt["slippage"])
    data = add_indicators(df, params)
    guard = RiskGuard(risk_cfg, {})
    cash = float(bt["starting_balance"])
    pos = None
    pending = None
    trades, equity_points = [], []

    def close_position(ts, price, reason):
        nonlocal cash, pos
        proceeds = pos["qty"] * price
        cash += proceeds * (1 - fee)
        pnl = proceeds * (1 - fee) - pos["cost"]
        trades.append({
            "entry_time": pos["time"], "exit_time": ts, "entry": pos["entry"], "exit": price,
            "qty": pos["qty"], "pnl": pnl, "return_pct": pnl / pos["cost"] * 100, "reason": reason,
        })
        pos = None

    start = warmup_bars(params)
    for i in range(start, len(data)):
        ts = data.index[i]
        o, h, l, c = (float(data[k].iat[i]) for k in ("open", "high", "low", "close"))
        atr_now = float(data["atr"].iat[i])

        if pending == "sell" and pos:
            close_position(ts, o * (1 - slip), "Signal")
        elif pending == "buy" and pos is None:
            entry = o * (1 + slip)
            stop, tp = initial_levels(entry, float(data["atr"].iat[i - 1]), params)
            equity = cash
            qty = position_size(equity, cash, entry, stop, risk_cfg)
            if qty > 0:
                cost = qty * entry * (1 + fee)
                cash -= cost
                pos = {"time": ts, "entry": entry, "qty": qty, "cost": cost,
                       "stop": stop, "tp": tp, "highest": entry}
        pending = None

        if pos:
            if o <= pos["stop"]:
                close_position(ts, o * (1 - slip), "Stop (Lücke)")
            elif l <= pos["stop"]:
                close_position(ts, pos["stop"] * (1 - slip), "Stop-Loss")
            elif pos["tp"] and h >= pos["tp"]:
                close_position(ts, max(o, pos["tp"]) * (1 - slip), "Take-Profit")

        equity = cash + (pos["qty"] * c if pos else 0.0)
        equity_points.append((ts, equity))
        guard.update(equity, ts.to_pydatetime())

        if guard.state["halted"]:
            if pos:
                close_position(ts, c * (1 - slip), "Notbremse Drawdown")
                equity_points[-1] = (ts, cash)
            break

        if pos:
            pos["highest"] = max(pos["highest"], h)
            pos["stop"] = trail_stop(pos["stop"], pos["highest"], atr_now, params)
            if bool(data["exit"].iat[i]):
                pending = "sell"
        elif bool(data["entry"].iat[i]):
            ok, _ = guard.can_open(equity, 0)
            if ok:
                pending = "buy"

    if pos:
        close_position(data.index[-1], float(data["close"].iat[-1]), "Ende Backtest")
        equity_points[-1] = (data.index[-1], cash)

    equity_series = pd.Series(dict(equity_points), name="equity")
    trades_df = pd.DataFrame(trades)
    result = BacktestResult(equity_series, trades_df)
    result.metrics = compute_metrics(equity_series, trades_df, data.iloc[start:], bt["starting_balance"])
    if guard.state["halted"]:
        result.metrics["Hinweis"] = guard.state["halt_reason"]
    return result


def compute_metrics(equity: pd.Series, trades: pd.DataFrame, prices: pd.DataFrame, start: float) -> dict:
    if equity.empty:
        return {"Trades": 0}
    final = float(equity.iloc[-1])
    drawdown = (equity / equity.cummax() - 1).min()
    rets = equity.pct_change().dropna()
    if len(equity) > 1:
        bar_seconds = (equity.index[-1] - equity.index[0]).total_seconds() / (len(equity) - 1)
        bars_per_year = 365 * 86400 / bar_seconds
    else:
        bars_per_year = 1
    sharpe = float(rets.mean() / rets.std() * np.sqrt(bars_per_year)) if rets.std() > 0 else 0.0
    metrics = {
        "Startkapital": round(start, 2),
        "Endkapital": round(final, 2),
        "Rendite %": round((final / start - 1) * 100, 2),
        "Buy & Hold %": round((prices["close"].iloc[-1] / prices["open"].iloc[0] - 1) * 100, 2),
        "Max. Drawdown %": round(float(drawdown) * 100, 2),
        "Sharpe Ratio": round(sharpe, 2),
        "Trades": int(len(trades)),
    }
    if len(trades):
        wins = trades[trades["pnl"] > 0]
        losses = trades[trades["pnl"] <= 0]
        gross_loss = -losses["pnl"].sum()
        metrics["Trefferquote %"] = round(len(wins) / len(trades) * 100, 1)
        metrics["Profit-Faktor"] = round(wins["pnl"].sum() / gross_loss, 2) if gross_loss > 0 else float("inf")
        metrics["Ø Gewinn"] = round(float(wins["pnl"].mean()), 2) if len(wins) else 0.0
        metrics["Ø Verlust"] = round(float(losses["pnl"].mean()), 2) if len(losses) else 0.0
    return metrics
