import pandas as pd

from config.settings import DEFAULT_SETTINGS
from strategies import indicators as ind
from strategies.ema_rsi import generate_signals, latest_action

P = DEFAULT_SETTINGS["strategy_params"]


def test_sma_ema():
    s = pd.Series(range(1, 11), dtype=float)
    assert ind.sma(s, 3).iloc[-1] == 9.0
    assert ind.ema(s, 3).iloc[-1] > ind.sma(s, 10).iloc[-1]


def test_rsi_bounds(ohlcv):
    r = ind.rsi(ohlcv["close"], 14).dropna()
    assert r.between(0, 100).all()
    up = pd.Series(range(50), dtype=float)
    assert ind.rsi(up, 14).iloc[-1] == 100.0


def test_bollinger_and_atr(ohlcv):
    bb = ind.bollinger(ohlcv["close"]).dropna()
    assert (bb["bb_upper"] >= bb["bb_mid"]).all() and (bb["bb_mid"] >= bb["bb_lower"]).all()
    assert (ind.atr(ohlcv["high"], ohlcv["low"], ohlcv["close"]).dropna() > 0).all()


def test_macd_columns(ohlcv):
    assert set(ind.macd(ohlcv["close"]).columns) == {"macd", "macd_signal", "macd_hist"}


def test_signals(ohlcv):
    data = generate_signals(ohlcv, P)
    assert set(data["position"].unique()) <= {0.0, 1.0}
    assert latest_action(ohlcv, P) in {"buy", "sell"}
    assert latest_action(ohlcv.head(5), P) == "hold"
