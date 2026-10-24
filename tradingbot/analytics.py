"""Kennzahlen für das Dashboard – reine Funktionen auf Trades und Kapitalverlauf."""
from collections import defaultdict
from datetime import datetime


def _hours(trade: dict) -> float:
    try:
        opened = datetime.fromisoformat(trade["opened_at"])
        closed = datetime.fromisoformat(trade["closed_at"])
        return (closed - opened).total_seconds() / 3600
    except (KeyError, ValueError):
        return 0.0


def trade_stats(trades: list[dict]) -> dict:
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_loss = -sum(losses)
    by_symbol = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
    daily = defaultdict(float)
    for t in trades:
        s = by_symbol[t["symbol"]]
        s["count"] += 1
        s["wins"] += t["pnl"] > 0
        s["pnl"] += t["pnl"]
        daily[t["closed_at"][:10]] += t["pnl"]
    count = len(trades)
    return {
        "count": count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / count * 100 if count else None,
        "total_pnl": round(sum(pnls), 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(sum(pnls) / count, 2) if count else None,
        "best": round(max(pnls), 2) if pnls else None,
        "worst": round(min(pnls), 2) if pnls else None,
        "avg_hold_hours": round(sum(_hours(t) for t in trades) / count, 1) if count else None,
        "by_symbol": [
            {"symbol": sym, "count": v["count"], "pnl": round(v["pnl"], 2),
             "win_rate": v["wins"] / v["count"] * 100}
            for sym, v in sorted(by_symbol.items(), key=lambda kv: -kv[1]["pnl"])
        ],
        "daily": [{"date": d, "pnl": round(p, 2)} for d, p in sorted(daily.items())],
    }


def max_drawdown(history: list) -> float | None:
    """Größter Rückgang vom Höchststand in Prozent (negativ), aus [[zeit, kapital], ...]."""
    peak, worst = None, 0.0
    for _, value in history:
        peak = value if peak is None else max(peak, value)
        if peak:
            worst = min(worst, (value / peak - 1) * 100)
    return round(worst, 2) if history else None


def benchmark_return(benchmark: dict | None, prices: dict) -> float | None:
    """Rendite in %, wenn man beim Start alle Paare zu gleichen Teilen gekauft und gehalten hätte."""
    if not benchmark:
        return None
    changes = [prices[s] / p - 1 for s, p in benchmark.get("prices", {}).items() if prices.get(s) and p]
    return round(sum(changes) / len(changes) * 100, 2) if changes else None
