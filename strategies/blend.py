"""Mezcla 50/50 de ``trend_sma`` y ``rsi2`` (parámetros fijos; resultado de la investigación Fase 1)."""
from __future__ import annotations

import pandas as pd

from strategies import rsi2, trend_sma

WEIGHTS = {"trend_sma": 0.5, "rsi2": 0.5}


def target_weights(closes: pd.DataFrame, regime: pd.Series) -> pd.DataFrame:
    return (WEIGHTS["trend_sma"] * trend_sma.target_weights(closes, regime)
            + WEIGHTS["rsi2"] * rsi2.target_weights(closes, regime))
