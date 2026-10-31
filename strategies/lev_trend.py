"""TQQQ con filtro de tendencia: 100 % TQQQ si QQQ > SMA200, si no liquidez. Solo en sombra.

Resultado de la investigación agresiva (2018-2026, señal al cierre y ejecución en la apertura
siguiente): CAGR 28 %, máxima caída -58 %. Es apalancado x3: puede perder la mitad en semanas.
Usa sus propios símbolos (QQQ, TQQQ), independientes de ``shadow_symbols``.
"""
from __future__ import annotations

import pandas as pd

from strategies.indicators import sma

PARAMS = {"asset": "TQQQ", "signal": "QQQ", "sma": 200}
SYMBOLS = ("QQQ", "TQQQ")
TRADABLE = ("TQQQ",)


def target_weights(closes: pd.DataFrame, regime: pd.Series | None = None, asset: str = PARAMS["asset"],
                   signal: str = PARAMS["signal"], ma: int = PARAMS["sma"]) -> pd.DataFrame:
    """Peso 1 en ``asset`` los días en que el cierre de ``signal`` supera su SMA(``ma``); si no, 0."""
    if signal not in closes or asset not in closes:
        raise ValueError(f"lev_trend necesita {signal} y {asset}")
    sig = closes[signal]
    on = (sig > sma(sig, ma)) & closes[asset].notna()
    return pd.DataFrame({asset: on.astype(float)}, index=closes.index)
