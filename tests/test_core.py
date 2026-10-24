from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from tradingbot.backtest import run_backtest
from tradingbot.data import synthetic
from tradingbot.indicators import atr, ema, rsi
from tradingbot.risk import RiskGuard, position_size
from tradingbot.strategy import add_indicators, initial_levels, trail_stop


def test_ema_matches_definition():
    s = pd.Series([1.0, 2.0, 3.0])
    alpha = 2 / (2 + 1)
    expected = 1.0
    expected = alpha * 2 + (1 - alpha) * expected
    expected = alpha * 3 + (1 - alpha) * expected
    assert ema(s, 2).iloc[-1] == pytest.approx(expected)


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1, 50, dtype=float))
    assert rsi(up).iloc[-1] == pytest.approx(100)
    df = synthetic(500)
    values = rsi(df["close"]).dropna()
    assert values.between(0, 100).all()


def test_atr_positive():
    assert (atr(synthetic(300)).iloc[1:] > 0).all()


def test_position_size_respects_risk(cfg):
    risk = cfg["risk"]
    qty = position_size(equity=1000, cash=1000, price=100, stop=95, risk_cfg=risk)
    # 1 % Risiko = 10 EUR / 5 EUR Abstand = 2 Stück, aber max. 30 % Position = 3 Stück
    assert qty == pytest.approx(2.0)
    assert qty * (100 - 95) <= 1000 * risk["risk_per_trade"] + 1e-9


def test_position_size_capped_by_max_position(cfg):
    qty = position_size(1000, 1000, price=100, stop=99.9, risk_cfg=cfg["risk"])
    assert qty * 100 <= 1000 * cfg["risk"]["max_position_pct"] + 1e-9


def test_position_size_too_small_returns_zero(cfg):
    assert position_size(20, 20, price=100, stop=95, risk_cfg=cfg["risk"]) == 0
    assert position_size(1000, 1000, price=100, stop=101, risk_cfg=cfg["risk"]) == 0


def test_risk_guard_daily_limit_and_drawdown(cfg):
    state = {}
    guard = RiskGuard(cfg["risk"], state)
    day = datetime(2024, 1, 1, tzinfo=timezone.utc)
    guard.update(1000, day)
    assert guard.can_open(1000, 0)[0]
    assert not guard.can_open(960, 0)[0]          # -4 % am Tag > 3 % Limit
    assert not guard.can_open(1000, 2)[0]         # max. 2 Positionen
    guard.update(840, day)                        # -16 % vom Hoch
    assert state["halted"]
    assert not guard.can_open(2000, 0)[0]


def test_trailing_stop_only_moves_up(cfg):
    p = cfg["strategy"]
    stop, tp = initial_levels(100, 2, p)
    assert stop == pytest.approx(96) and tp == pytest.approx(108)
    assert trail_stop(stop, 110, 2, p) == pytest.approx(105)
    assert trail_stop(105, 100, 2, p) == pytest.approx(105)


def test_signals_do_not_use_future_data(cfg):
    df = synthetic(1500)
    full = add_indicators(df, cfg["strategy"])
    partial = add_indicators(df.iloc[:1000], cfg["strategy"])
    cols = ["entry", "exit", "ema_fast", "rsi", "atr"]
    pd.testing.assert_frame_equal(full[cols].iloc[:1000], partial[cols])


def test_backtest_runs_and_is_consistent(cfg):
    result = run_backtest(synthetic(4000), cfg)
    m = result.metrics
    assert m["Trades"] > 0
    assert m["Endkapital"] == pytest.approx(result.equity.iloc[-1], abs=0.01)
    pnl_sum = result.trades["pnl"].sum()
    assert m["Endkapital"] == pytest.approx(cfg["backtest"]["starting_balance"] + pnl_sum, abs=0.01)
    # kein Trade darf mehr als ~risk_per_trade verlieren (plus Gebühren/Lücken-Puffer)
    worst = result.trades["pnl"].min()
    assert worst > -cfg["backtest"]["starting_balance"] * 0.03
