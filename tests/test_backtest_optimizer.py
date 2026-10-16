import pandas as pd

from backtesting.engine import run_backtest
from backtesting.metrics import max_drawdown, profit_factor, sharpe_ratio, win_rate
from config.settings import DEFAULT_SETTINGS
from optimization.optimizer import optimize, run_feedback_loop

P = DEFAULT_SETTINGS["strategy_params"]


def test_metrics_basic():
    assert max_drawdown(pd.Series([100, 120, 90, 130])) == -0.25
    pnl = pd.Series([1.0, -0.5, 2.0])
    assert profit_factor(pnl) == 6.0
    assert abs(win_rate(pnl) - 2 / 3) < 1e-9
    assert sharpe_ratio(pd.Series([0.0, 0.0])) == 0.0


def test_backtest_runs(ohlcv):
    res = run_backtest(ohlcv, P)
    assert len(res.equity) == len(ohlcv)
    for k in ("sharpe", "profit_factor", "max_drawdown", "win_rate", "num_trades"):
        assert k in res.metrics
    assert res.metrics["num_trades"] == len(res.trades)


def test_optimize_small(ohlcv):
    out = optimize(ohlcv, P, n_trials=5)
    assert "ema_fast" in out["best_params"]
    assert out["best_params"]["ema_slow"] > out["best_params"]["ema_fast"]


def test_feedback_loop_saves(store, provider):
    store.update({"symbols": ["AAPL"]})
    results = run_feedback_loop(store, provider, n_trials=5)
    assert len(results) == 1
    assert store.optimization_history()
