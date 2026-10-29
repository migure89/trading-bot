"""Registro de estrategias.

- ``ema_rsi``: estrategia por símbolo que **opera** (la única que envía órdenes).
- ``trend_sma``, ``rsi2`` y ``blend``: estrategias de cartera sobre barras diarias que,
  por ahora, solo se ejecutan **en sombra** (señales y pesos objetivo, nunca órdenes).
  Sus parámetros son fijos: el optimizador no los toca ni los aplica.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from strategies import blend, ema_rsi, rsi2, trend_sma


@dataclass(frozen=True)
class StrategySpec:
    name: str
    kind: str                         # "symbol" (señal por símbolo) | "portfolio" (pesos objetivo)
    description: str
    func: Callable[..., Any]
    params: dict = field(default_factory=dict)
    trades: bool = False              # True solo para la estrategia que envía órdenes


TRADING_STRATEGY = "ema_rsi"

STRATEGIES: dict[str, StrategySpec] = {
    "ema_rsi": StrategySpec("ema_rsi", "symbol", "EMA 12/26 + RSI14 + Bollinger (optimizable)",
                            ema_rsi.latest_action, trades=True),
    "trend_sma": StrategySpec("trend_sma", "portfolio", "Tendencia SMA 50/200 + filtro SPY > SMA200",
                              trend_sma.target_weights, dict(trend_sma.PARAMS)),
    "rsi2": StrategySpec("rsi2", "portfolio", "RSI(2) < 10, salida cierre > SMA5, máx. 4 posiciones",
                         rsi2.target_weights, dict(rsi2.PARAMS)),
    "blend": StrategySpec("blend", "portfolio", "Mezcla 50 % trend_sma + 50 % rsi2",
                          blend.target_weights, dict(blend.WEIGHTS)),
}

SHADOW_STRATEGIES = tuple(n for n, s in STRATEGIES.items() if s.kind == "portfolio")
SHADOW_CHOICES = ("off", *SHADOW_STRATEGIES)


def get_strategy(name: str) -> StrategySpec:
    try:
        return STRATEGIES[name]
    except KeyError:
        raise ValueError(f"Estrategia desconocida: {name} (disponibles: {', '.join(STRATEGIES)})") from None


def portfolio_weights(name: str, closes: pd.DataFrame, regime: pd.Series) -> pd.DataFrame:
    """Pesos objetivo (fila = día, columna = símbolo) de una estrategia de cartera."""
    spec = get_strategy(name)
    if spec.kind != "portfolio":
        raise ValueError(f"{name} no es una estrategia de cartera")
    return spec.func(closes, regime)
