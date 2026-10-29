"""Estrategias de cartera diarias (trend_sma, rsi2, blend), registro y simulador de cartera."""
import numpy as np
import pandas as pd
import pytest

from backtesting.portfolio import backtest_strategy, simulate
from strategies import registry, rsi2, trend_sma
from strategies.portfolio import market_regime, sleeves


def _index(n):
    return pd.bdate_range("2024-01-02", periods=n, tz="America/New_York").tz_convert("UTC")


def _panel(n=500, k=6, seed=1):
    rng = np.random.default_rng(seed)
    data = {f"S{i}": 100 * np.exp(np.cumsum(rng.normal(0.0006, 0.015, n))) for i in range(k)}
    return pd.DataFrame(data, index=_index(n))


def _bull(index):
    return pd.Series(True, index=index)


# ---- registro ---------------------------------------------------------------------
def test_registry():
    assert set(registry.STRATEGIES) == {"ema_rsi", "trend_sma", "rsi2", "blend"}
    assert registry.SHADOW_STRATEGIES == ("trend_sma", "rsi2", "blend")
    assert registry.SHADOW_CHOICES[0] == "off"
    traders = [n for n, s in registry.STRATEGIES.items() if s.trades]
    assert traders == [registry.TRADING_STRATEGY] == ["ema_rsi"]
    expected = {"entry": 10.0, "exit_ma": 5, "max_pos": 4, "trend_ma": 200}
    assert registry.get_strategy("rsi2").params == expected
    with pytest.raises(ValueError):
        registry.get_strategy("magic")
    with pytest.raises(ValueError):
        registry.portfolio_weights("ema_rsi", _panel(), _bull(_panel().index))


# ---- sin look-ahead ---------------------------------------------------------------
@pytest.mark.parametrize("name", ["trend_sma", "rsi2", "blend"])
def test_no_lookahead(name):
    closes = _panel(520)
    spy = closes["S0"]
    full = registry.portfolio_weights(name, closes, market_regime(spy, closes.index))
    for cut in (260, 333, 410, 519):
        part = closes.iloc[:cut]
        w = registry.portfolio_weights(name, part, market_regime(spy.iloc[:cut], part.index))
        pd.testing.assert_series_equal(w.iloc[-1], full.iloc[cut - 1], check_names=False)


def test_future_shock_does_not_change_past_weights():
    closes = _panel(400)
    shocked = closes.copy()
    shocked.iloc[300:] *= 0.3
    reg = _bull(closes.index)
    a = registry.portfolio_weights("blend", closes, reg).iloc[:300]
    b = registry.portfolio_weights("blend", shocked, reg).iloc[:300]
    pd.testing.assert_frame_equal(a, b)


def test_simulator_executes_next_open():
    idx = _index(5)
    opens = pd.DataFrame({"A": [10, 10, 20, 20, 20.0]}, index=idx)
    closes = pd.DataFrame({"A": [10, 10, 20, 20, 20.0]}, index=idx)
    target = pd.DataFrame({"A": [0, 1, 1, 1, 1.0]}, index=idx)   # decidido al cierre del día 1
    res = simulate(opens, closes, target, cost_bps=0)
    # Compra en la apertura del día 2 (20): no captura el salto 10->20 del propio día de la señal.
    assert res.equity.iloc[-1] == pytest.approx(100_000)


# ---- RSI(2) -------------------------------------------------------------------------
def _uptrend_with_dip(n=260, dip_at=240):
    close = np.linspace(100, 200, n)
    close[dip_at:dip_at + 3] = close[dip_at - 1] * np.array([0.97, 0.94, 0.92])   # 3 días de caída
    close[dip_at + 3:] = close[dip_at - 1] * 1.05                                  # rebote
    return pd.DataFrame({"A": close}, index=_index(n))


def test_rsi2_entry_and_exit():
    closes = _uptrend_with_dip()
    w = rsi2.target_weights(closes, _bull(closes.index))["A"]
    assert (w.iloc[:240] == 0).all()             # tendencia sin retrocesos: RSI2 alto, fuera
    # entra tras la caída (RSI2 < 10, cierre > SMA200); con un único símbolo el peso es 1/N = 1
    assert w.iloc[241] == pytest.approx(1.0)
    assert w.iloc[243] == 0                      # sale al cerrar sobre la SMA5 en el rebote
    state = rsi2.signal_state(closes, _bull(closes.index))["A"]
    assert state.iloc[241] and not state.iloc[243]


def test_rsi2_requires_symbol_above_sma200():
    closes = _uptrend_with_dip()
    closes.iloc[:] = closes.iloc[::-1].to_numpy()                         # tendencia bajista
    closes.iloc[240:243, 0] = closes.iloc[239, 0] * np.array([0.97, 0.94, 0.92])
    w = rsi2.target_weights(closes, _bull(closes.index))
    assert (w["A"] == 0).all()


def test_regime_blocks_entries():
    closes = _uptrend_with_dip()
    bear = pd.Series(False, index=closes.index)
    assert (rsi2.target_weights(closes, bear) == 0).all().all()
    assert (trend_sma.target_weights(closes, bear) == 0).all().all()
    assert trend_sma.target_weights(closes, _bull(closes.index))["A"].iloc[-1] == 1.0


def test_market_regime_spy_above_sma200():
    up = pd.Series(np.linspace(100, 200, 300), index=_index(300))
    down = pd.Series(np.linspace(200, 100, 300), index=_index(300))
    assert market_regime(up).iloc[-1] and not market_regime(up).iloc[100]   # sin SMA200 aún: False
    assert not market_regime(down).iloc[-1]


def test_trend_sma_golden_cross():
    n = 400
    close = np.r_[np.linspace(200, 100, 200), np.linspace(100, 260, n - 200)]
    closes = pd.DataFrame({"A": close, "B": np.linspace(300, 100, n)}, index=_index(n))
    w = trend_sma.target_weights(closes, _bull(closes.index))
    assert w["A"].iloc[-1] == 0.5 and w["B"].iloc[-1] == 0.0
    assert w["A"].iloc[210] == 0.0


# ---- límite de posiciones ---------------------------------------------------------
def test_max_four_positions_ranked_by_lowest_rsi2():
    on = pd.DataFrame(True, index=_index(3), columns=list("ABCDEF"))
    rank = pd.DataFrame([[5, 1, 9, 3, 2, 8]] * 3, index=on.index, columns=on.columns, dtype=float)
    w = sleeves(on, max_pos=4, rank=rank)
    assert (w.sum(axis=1) == 1.0).all()
    assert list(w.columns[w.iloc[0] > 0]) == ["A", "B", "D", "E"]


def test_rsi2_never_exceeds_four_positions():
    w = rsi2.target_weights(_panel(800, k=10, seed=3), _bull(_panel(800, k=10, seed=3).index))
    assert ((w > 0).sum(axis=1) <= 4).all() and (w.sum(axis=1) <= 1 + 1e-9).all()
    assert (w > 0).any().any()


def test_blend_is_half_and_half():
    closes = _panel(500)
    reg = _bull(closes.index)
    expected = 0.5 * trend_sma.target_weights(closes, reg) + 0.5 * rsi2.target_weights(closes, reg)
    pd.testing.assert_frame_equal(registry.portfolio_weights("blend", closes, reg), expected)
    assert (registry.portfolio_weights("blend", closes, reg).sum(axis=1) <= 1 + 1e-9).all()


def test_backtest_strategy_runs_on_daily_bars():
    closes = _panel(600, k=4)
    closes["SPY"] = closes.mean(axis=1)
    bars = {s: pd.DataFrame({"open": closes[s], "close": closes[s]}) for s in closes}
    res = backtest_strategy(bars, "blend")
    m = res.metrics
    assert set(m) >= {"cagr", "sharpe", "max_drawdown", "num_trades"}
    assert len(res.equity) == 600 and np.isfinite(m["sharpe"])
