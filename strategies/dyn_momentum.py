"""Momentum ajustado por riesgo sobre un universo dinámico (los 100 valores más líquidos).

Puntuación = retorno de t-126 a t-21 / volatilidad anualizada de 63 sesiones; top 10 a peso
igual, rebalanceo semanal, sin filtro de régimen. Solo puntúan los valores con volatilidad anualizada
>= ``min_vol`` (15 % por defecto; ``SHADOW_MIN_VOL``): dividir por una volatilidad casi nula (liquidez,
letras del Tesoro) dispararía la puntuación. **Solo seguimiento** (en sombra): en la
investigación (CAGR 12.7 %, máxima caída -46 %) quedó por debajo de comprar QQQ.
El universo lo construye ``core.universe`` (o ``shadow_symbols`` con ``shadow_universe=fixed``).
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from monitoring.logger import get_logger
from strategies.portfolio import hold_between_rebalances, weekly_rebalance_days

log = get_logger("dyn_momentum")

DEFAULT_MIN_VOL = 0.15
PARAMS = {"top": 10, "lookback": 126, "skip": 21, "vol_window": 63, "universe_top": 100,
          "rebalance": "semanal", "regime_filter": False, "min_vol": DEFAULT_MIN_VOL}


def min_vol_from_env() -> float:
    """Volatilidad anualizada mínima (``SHADOW_MIN_VOL``, fracción: 0.15 = 15 %; 0 la desactiva)."""
    raw = os.getenv("SHADOW_MIN_VOL", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_MIN_VOL
    except ValueError:
        value = -1.0
    if not 0 <= value < 5:
        log.warning("SHADOW_MIN_VOL inválido; se usa el valor por defecto",
                    extra={"value": raw, "default": DEFAULT_MIN_VOL})
        return DEFAULT_MIN_VOL
    return value


def params() -> dict:
    """Parámetros efectivos (con ``min_vol`` del entorno), para el snapshot de la sombra."""
    return {**PARAMS, "min_vol": min_vol_from_env()}


def score(closes: pd.DataFrame, lookback: int = PARAMS["lookback"], skip: int = PARAMS["skip"],
          vol_window: int = PARAMS["vol_window"], min_vol: float | None = None) -> pd.DataFrame:
    min_vol = min_vol_from_env() if min_vol is None else min_vol
    ret = closes.pct_change(fill_method=None)
    vol = ret.rolling(vol_window).std() * np.sqrt(252)
    mom = closes.shift(skip) / closes.shift(lookback) - 1
    valid = closes.notna().rolling(lookback + 1).sum() == lookback + 1   # sin huecos en la ventana
    valid &= vol >= min_vol                                              # fuera casi-liquidez
    return (mom / vol.replace(0.0, np.nan)).where(valid)


def target_weights(closes: pd.DataFrame, regime: pd.Series | None = None, top: int = PARAMS["top"],
                   **kw) -> pd.DataFrame:
    sc = score(closes, **kw)
    rank = sc.rank(axis=1, ascending=False, method="first")
    pick = ((rank <= top) & sc.notna()).astype(float) / top
    return hold_between_rebalances(pick, weekly_rebalance_days(closes.index))
