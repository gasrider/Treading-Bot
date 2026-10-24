"""Positionsgröße und Schutzmechanismen (Tagesverlust-Limit, Drawdown-Notbremse)."""
from datetime import datetime, timezone


def position_size(equity: float, cash: float, price: float, stop: float, risk_cfg: dict) -> float:
    """Menge so wählen, dass ein Stop-Treffer höchstens risk_per_trade des Kontos kostet."""
    stop_distance = price - stop
    if stop_distance <= 0 or price <= 0:
        return 0.0
    qty = equity * risk_cfg["risk_per_trade"] / stop_distance
    max_value = min(equity * risk_cfg["max_position_pct"], cash * 0.98)
    qty = min(qty, max_value / price)
    if qty * price < risk_cfg["min_order_value"]:
        return 0.0
    return qty


class RiskGuard:
    """Hält Tagesstart- und Höchst-Kapital im (persistierten) state-Dict nach."""

    def __init__(self, risk_cfg: dict, state: dict):
        self.cfg = risk_cfg
        self.state = state
        state.setdefault("halted", False)
        state.setdefault("halt_reason", "")

    def update(self, equity: float, now: datetime | None = None) -> None:
        today = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        if self.state.get("day") != today:
            self.state["day"] = today
            self.state["day_start_equity"] = equity
        self.state["peak_equity"] = max(self.state.get("peak_equity", equity), equity)
        if equity < self.state["peak_equity"] * (1 - self.cfg["max_drawdown"]):
            if not self.state["halted"]:
                self.state["halted"] = True
                self.state["halt_reason"] = (
                    f"Max. Drawdown erreicht: Kapital {equity:.2f} < "
                    f"{(1 - self.cfg['max_drawdown']) * 100:.0f} % vom Hoch {self.state['peak_equity']:.2f}"
                )

    def daily_limit_hit(self, equity: float) -> bool:
        start = self.state.get("day_start_equity", equity)
        return equity < start * (1 - self.cfg["max_daily_loss"])

    def can_open(self, equity: float, open_positions: int) -> tuple[bool, str]:
        if self.state["halted"]:
            return False, "Bot gestoppt: " + self.state["halt_reason"]
        if self.daily_limit_hit(equity):
            return False, "Tagesverlust-Limit erreicht"
        if open_positions >= self.cfg["max_open_positions"]:
            return False, "Maximale Anzahl offener Positionen erreicht"
        return True, ""
