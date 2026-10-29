"""Tendencia SMA 50/200 con filtro de régimen (SPY > SMA200). Parámetros fijos (no se optimizan)."""
from __future__ import annotations

import pandas as pd

from strategies.indicators import sma
from strategies.portfolio import apply_regime, sleeves

PARAMS = {"fast": 50, "slow": 200}


def target_weights(closes: pd.DataFrame, regime: pd.Series, fast: int = PARAMS["fast"],
                   slow: int = PARAMS["slow"]) -> pd.DataFrame:
    """Peso 1/N en cada símbolo con SMA(fast) > SMA(slow) y régimen alcista; 0 en el resto."""
    on = closes.apply(lambda x: sma(x, fast)) > closes.apply(lambda x: sma(x, slow))
    return sleeves(apply_regime(on, regime))
