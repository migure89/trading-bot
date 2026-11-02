"""Rotación semanal entre ETFs apalancados x3 con filtro SPY > SMA200. Solo en sombra.

El último día hábil de cada semana, si SPY > SMA200, 100 % en el candidato con mayor retorno
a 63 sesiones; si no, liquidez. La posición se mantiene hasta el siguiente rebalanceo.
Investigación agresiva (2018-2026): CAGR 21 %, máxima caída -65 %; es la de mayor probabilidad
de un mes de +25 % (~11 %) y también la más volátil.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.portfolio import hold_between_rebalances, weekly_rebalance_days

PARAMS = {"candidates": ["TQQQ", "SOXL", "UPRO", "TECL", "TNA"], "lookback": 63, "regime": "SPY>SMA200"}
TRADABLE = tuple(PARAMS["candidates"])
SYMBOLS = ("SPY", *TRADABLE)


def target_weights(closes: pd.DataFrame, regime: pd.Series,
                   lookback: int = PARAMS["lookback"]) -> pd.DataFrame:
    cands = [c for c in TRADABLE if c in closes]
    if not cands:
        raise ValueError("lev_rotation: sin datos de los ETFs candidatos")
    px = closes[cands]
    mom = px / px.shift(lookback) - 1
    has = mom.notna().any(axis=1)
    best = pd.Series(None, index=px.index, dtype=object)
    if has.any():
        best[has] = mom[has].fillna(-np.inf).idxmax(axis=1)
    pick = pd.DataFrame(0.0, index=px.index, columns=cands)
    for col in cands:
        pick.loc[best == col, col] = 1.0
    bull = regime.reindex(px.index).fillna(False).astype(bool)
    pick[~bull.to_numpy()] = 0.0
    return hold_between_rebalances(pick, weekly_rebalance_days(px.index))
