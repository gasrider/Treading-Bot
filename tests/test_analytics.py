import pytest

from tradingbot.analytics import benchmark_return, max_drawdown, trade_stats


def trade(sym, pnl, opened="2026-01-01T10:00:00+00:00", closed="2026-01-01T14:00:00+00:00"):
    return {"symbol": sym, "pnl": pnl, "opened_at": opened, "closed_at": closed}


def test_trade_stats():
    s = trade_stats([trade("BTC/EUR", 30), trade("BTC/EUR", -10), trade("ETH/EUR", -10, closed="2026-01-02T10:00:00+00:00")])
    assert s["count"] == 3 and s["wins"] == 1 and s["losses"] == 2
    assert s["win_rate"] == pytest.approx(100 / 3)
    assert s["profit_factor"] == 1.5 and s["expectancy"] == pytest.approx(3.33, abs=0.01)
    assert s["avg_win"] == 30 and s["avg_loss"] == -10 and s["best"] == 30 and s["worst"] == -10
    assert s["by_symbol"][0] == {"symbol": "BTC/EUR", "count": 2, "pnl": 20, "win_rate": 50}
    assert s["daily"] == [{"date": "2026-01-01", "pnl": 20}, {"date": "2026-01-02", "pnl": -10}]
    assert s["avg_hold_hours"] == pytest.approx((4 + 4 + 24) / 3, abs=0.1)


def test_trade_stats_empty():
    s = trade_stats([])
    assert s["count"] == 0 and s["win_rate"] is None and s["profit_factor"] is None


def test_max_drawdown():
    assert max_drawdown([["a", 100], ["b", 120], ["c", 90], ["d", 130]]) == -25.0
    assert max_drawdown([]) is None


def test_benchmark_return():
    bench = {"prices": {"BTC/EUR": 100, "ETH/EUR": 200}}
    assert benchmark_return(bench, {"BTC/EUR": 110, "ETH/EUR": 180}) == 0.0
    assert benchmark_return(bench, {"BTC/EUR": 120}) == 20.0
    assert benchmark_return(None, {}) is None
