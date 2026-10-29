"""Reversión a la media RSI(2) (Connors) con filtro de tendencia y de régimen. Parámetros fijos.

- Entrada: RSI(2) < 10, cierre > SMA200 del propio símbolo y SPY > SMA200.
- Salida: cierre > SMA5.
- Como máximo 4 posiciones (1/4 cada una); si hay más candidatas, entran las de RSI(2) más bajo.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.indicators import rsi, sma
from strategies.portfolio import apply_regime, sleeves

PARAMS = {"entry": 10.0, "exit_ma": 5, "max_pos": 4, "trend_ma": 200}


def signal_state(closes: pd.DataFrame, regime: pd.Series, entry: float = PARAMS["entry"],
                 exit_ma: int = PARAMS["exit_ma"], trend_ma: int = PARAMS["trend_ma"]) -> pd.DataFrame:
    """Estado deseado por símbolo (True = dentro) antes de aplicar el límite de posiciones."""
    r2 = closes.apply(lambda x: rsi(x, 2))
    up = closes > closes.apply(lambda x: sma(x, trend_ma))
    ent = apply_regime((r2 < entry) & up, regime)
    ext = closes > closes.apply(lambda x: sma(x, exit_ma))
    state = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns)
    state[ext] = 0.0
    state[ent] = 1.0   # la entrada prevalece (con RSI2 < 10 el cierre ya está bajo la SMA5)
    return state.ffill().fillna(0.0) > 0


def target_weights(closes: pd.DataFrame, regime: pd.Series, entry: float = PARAMS["entry"],
                   exit_ma: int = PARAMS["exit_ma"], max_pos: int = PARAMS["max_pos"],
                   trend_ma: int = PARAMS["trend_ma"]) -> pd.DataFrame:
    on = signal_state(closes, regime, entry, exit_ma, trend_ma)
    rank = closes.apply(lambda x: rsi(x, 2))
    return sleeves(on, max_pos, rank=rank)
