"""Registro de estrategias.

- ``ema_rsi``: estrategia por símbolo que **opera** (la única que envía órdenes).
- ``trend_sma``, ``rsi2`` y ``blend``: estrategias de cartera sobre barras diarias que,
  por ahora, solo se ejecutan **en sombra** (señales y pesos objetivo, nunca órdenes).
- ``lev_trend``, ``lev_rotation`` y ``dyn_momentum``: estrategias agresivas de la investigación
  (ETFs apalancados x3 y universo dinámico), también **solo en sombra** (``trades=False``).

Los parámetros de las estrategias de cartera son fijos: el optimizador no los toca ni los aplica.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from strategies import blend, dyn_momentum, ema_rsi, lev_rotation, lev_trend, rsi2, trend_sma

UNIVERSE_KINDS = ("shadow", "own", "dynamic")


@dataclass(frozen=True)
class StrategySpec:
    name: str
    kind: str                         # "symbol" (señal por símbolo) | "portfolio" (pesos objetivo)
    description: str
    func: Callable[..., Any]
    params: dict = field(default_factory=dict)
    trades: bool = False              # True solo para la estrategia que envía órdenes
    # Universo en sombra: "shadow" (= shadow_symbols), "own" (``symbols`` propios, p. ej. ETFs
    # apalancados, independientes de shadow_symbols) o "dynamic" (escáner ``core.universe``).
    universe: str = "shadow"
    symbols: tuple[str, ...] = ()     # símbolos a descargar con universe="own"
    tradable: tuple[str, ...] = ()    # columnas con peso (vacío = todos los símbolos del panel)
    params_fn: Callable[[], dict] | None = None   # parámetros efectivos (p. ej. leídos del entorno)

    def current_params(self) -> dict:
        return self.params_fn() if self.params_fn else self.params


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
    "lev_trend": StrategySpec("lev_trend", "portfolio", "100 % TQQQ si QQQ > SMA200; si no, liquidez",
                              lev_trend.target_weights, dict(lev_trend.PARAMS), universe="own",
                              symbols=lev_trend.SYMBOLS, tradable=lev_trend.TRADABLE),
    "lev_rotation": StrategySpec("lev_rotation", "portfolio",
                                 "Rotación semanal al ETF x3 con mayor retorno a 63 días si SPY > SMA200",
                                 lev_rotation.target_weights, dict(lev_rotation.PARAMS), universe="own",
                                 symbols=lev_rotation.SYMBOLS, tradable=lev_rotation.TRADABLE),
    "dyn_momentum": StrategySpec("dyn_momentum", "portfolio",
                                 "Top 10 por momentum ajustado por riesgo de los 100 más líquidos, "
                                 "sin casi-liquidez (vol ≥ 15 %, semanal)",
                                 dyn_momentum.target_weights, dict(dyn_momentum.PARAMS), universe="dynamic",
                                 params_fn=dyn_momentum.params),
}

SHADOW_STRATEGIES = tuple(n for n, s in STRATEGIES.items() if s.kind == "portfolio")
SHADOW_CHOICES = ("off", *SHADOW_STRATEGIES)
# Estrategias que la migración ``2026-10-shadow-agresiva`` añade a la sombra de las BDs existentes.
AGGRESSIVE_SHADOW = ("lev_trend", "lev_rotation", "dyn_momentum")
DEFAULT_SHADOW_STRATEGIES = ("blend", *AGGRESSIVE_SHADOW)
SHADOW_UNIVERSES = ("fixed", "dynamic")   # universo de dyn_momentum: shadow_symbols o escáner


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
