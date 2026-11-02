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


def weekly_rebalance_days(index: pd.Index) -> pd.Series:
    """True en la última sesión de cada semana (hora de Nueva York).

    Una fila es fin de semana si la siguiente cae en otra semana ISO; la última fila del panel
    solo cuenta si es viernes (con la semana aún abierta no se sabe si habrá más sesiones).
    """
    if len(index) == 0:
        return pd.Series(dtype=bool)
    idx = pd.DatetimeIndex(index)
    local = idx.tz_convert("America/New_York") if idx.tz is not None else idx
    iso = local.isocalendar()
    week = (iso["year"].astype(int) * 100 + iso["week"].astype(int)).to_numpy()
    last = np.zeros(len(idx), bool)
    last[:-1] = week[:-1] != week[1:]
    last[-1] = local[-1].weekday() == 4
    return pd.Series(last, index=index)


def hold_between_rebalances(decisions: pd.DataFrame, rebalance: pd.Series) -> pd.DataFrame:
    """Pesos decididos en los días de rebalanceo y mantenidos hasta el siguiente (antes: 0)."""
    mask = np.asarray(rebalance, dtype=bool)
    held = decisions.astype(float).copy()
    held.iloc[~mask] = np.nan
    return held.ffill().fillna(0.0)
