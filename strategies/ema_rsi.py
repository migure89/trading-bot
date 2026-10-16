"""Estrategia de cruce de EMAs filtrada por RSI y Bollinger.

- Entrada larga: EMA rápida > EMA lenta, RSI < sobrecompra y cierre < banda superior.
- Salida: EMA rápida < EMA lenta o RSI > sobrecompra.
Devuelve una posición objetivo (1 = largo, 0 = fuera) por barra.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.indicators import add_all


def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    data = add_all(df, params)
    trend_up = data["ema_fast"] > data["ema_slow"]
    entry = trend_up & (data["rsi"] < params["rsi_overbought"]) & (data["close"] < data["bb_upper"])
    exit_ = (~trend_up) | (data["rsi"] > params["rsi_overbought"])
    raw = pd.Series(np.nan, index=data.index)
    raw[entry] = 1.0
    raw[exit_] = 0.0
    data["position"] = raw.ffill().fillna(0.0)
    data["signal"] = data["position"].diff().fillna(data["position"])  # +1 compra, -1 venta
    return data


def latest_action(df: pd.DataFrame, params: dict) -> str:
    """Acción para la última barra: 'buy', 'sell' o 'hold'."""
    if len(df) < max(int(params["ema_slow"]), int(params.get("bb_period", 20))) + 2:
        return "hold"
    pos = generate_signals(df, params)["position"].iloc[-1]
    return "buy" if pos == 1 else "sell"
