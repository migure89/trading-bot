"""Momentum ajustado por riesgo sobre un universo dinámico (los 100 valores más líquidos).

Puntuación = retorno de t-126 a t-21 / volatilidad anualizada de 63 sesiones; top 10 a peso
igual, rebalanceo semanal, sin filtro de régimen. **Solo seguimiento** (en sombra): en la
investigación (CAGR 12.7 %, máxima caída -46 %) quedó por debajo de comprar QQQ.
El universo lo construye ``core.universe`` (o ``shadow_symbols`` con ``shadow_universe=fixed``).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.portfolio import hold_between_rebalances, weekly_rebalance_days

PARAMS = {"top": 10, "lookback": 126, "skip": 21, "vol_window": 63, "universe_top": 100,
          "rebalance": "semanal", "regime_filter": False}


def score(closes: pd.DataFrame, lookback: int = PARAMS["lookback"], skip: int = PARAMS["skip"],
          vol_window: int = PARAMS["vol_window"]) -> pd.DataFrame:
    ret = closes.pct_change(fill_method=None)
    vol = ret.rolling(vol_window).std() * np.sqrt(252)
    mom = closes.shift(skip) / closes.shift(lookback) - 1
    valid = closes.notna().rolling(lookback + 1).sum() == lookback + 1   # sin huecos en la ventana
    return (mom / vol.replace(0.0, np.nan)).where(valid)


def target_weights(closes: pd.DataFrame, regime: pd.Series | None = None, top: int = PARAMS["top"],
                   **kw) -> pd.DataFrame:
    sc = score(closes, **kw)
    rank = sc.rank(axis=1, ascending=False, method="first")
    pick = ((rank <= top) & sc.notna()).astype(float) / top
    return hold_between_rebalances(pick, weekly_rebalance_days(closes.index))
