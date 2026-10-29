"""Utilidades comunes de las estrategias de cartera (barras diarias, sin look-ahead).

Convención: los pesos de la fila ``t`` se calculan solo con cierres hasta ``t`` y se
ejecutarían en la apertura de ``t+1``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.indicators import sma

REGIME_SYMBOL = "SPY"
REGIME_MA = 200


def market_regime(benchmark_close: pd.Series, index: pd.Index | None = None,
                  ma: int = REGIME_MA) -> pd.Series:
    """Régimen alcista: cierre del índice de referencia (SPY) > su SMA(``ma``). Sin datos = False."""
    close = benchmark_close.dropna()
    on = (close > sma(close, ma)).astype(bool)
    if index is not None:
        on = on.reindex(index).ffill()
    return on.fillna(False).astype(bool)


def apply_regime(on: pd.DataFrame, regime: pd.Series) -> pd.DataFrame:
    reg = regime.reindex(on.index).fillna(False).astype(bool).to_numpy()
    return on & reg[:, None]


def sleeves(on: pd.DataFrame, max_pos: int | None = None, rank: pd.DataFrame | None = None) -> pd.DataFrame:
    """Señales booleanas por símbolo -> pesos: 1/N por símbolo, o 1/``max_pos`` con cupos.

    Con cupos se mantienen las posiciones abiertas y las nuevas entran por mejor ``rank``
    (menor = mejor); nunca hay más de ``max_pos`` posiciones a la vez.
    """
    on = on.fillna(False).astype(bool)
    n = on.shape[1]
    if n == 0:
        return on.astype(float)
    if not max_pos or max_pos >= n:
        return on.astype(float) / n
    held = np.zeros(n, bool)
    out = np.zeros(on.shape)
    a = on.to_numpy()
    rk = rank.reindex_like(on).to_numpy() if rank is not None else np.zeros(on.shape)
    for i in range(len(a)):
        held &= a[i]
        free = max_pos - int(held.sum())
        cand = np.where(a[i] & ~held)[0]
        if free > 0 and len(cand):
            cand = cand[np.argsort(np.nan_to_num(rk[i, cand], nan=1e9), kind="stable")][:free]
            held[cand] = True
        out[i] = held / max_pos
    return pd.DataFrame(out, index=on.index, columns=on.columns)
