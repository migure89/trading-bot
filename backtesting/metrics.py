"""Métricas de rendimiento: Sharpe, Profit Factor, Max Drawdown y Win Rate."""
from __future__ import annotations

import numpy as np
import pandas as pd

PERIODS_PER_YEAR = {"1Min": 252 * 390, "5Min": 252 * 78, "15Min": 252 * 26,
                    "1Hour": 252 * 7, "1Day": 252}   # sesión regular de acciones (referencia)


def infer_periods_per_year(index, timeframe: str = "1Hour") -> int:
    """Periodos por año según las barras reales: mediana de barras por día × días de mercado.

    Con sesión regular salen ~7 barras/día en 1Hour (1764/año); con horario extendido ~16; cripto
    (24/7, con fines de semana) usa 365 días. Si no hay suficientes datos, la tabla de referencia.
    """
    fallback = PERIODS_PER_YEAR.get(timeframe, 252)
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        return fallback
    local = index.tz_convert("America/New_York") if index.tz is not None else index
    days = pd.Series(1, index=local).groupby(local.date).sum()
    if len(days) < 2:
        return fallback
    full = days.iloc[1:-1] if len(days) > 3 else days          # el primer/último día pueden ser parciales
    per_day = max(1, int(round(float(full.median()))))
    weekends = any(pd.Timestamp(d).weekday() >= 5 for d in days.index)
    return per_day * (365 if weekends else 252)


def sharpe_ratio(returns: pd.Series, periods_per_year: int = 252) -> float:
    r = returns.dropna()
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1) * np.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> float:
    """Devuelve el drawdown máximo como fracción negativa (p.ej. -0.15)."""
    if equity.empty:
        return 0.0
    return float((equity / equity.cummax() - 1).min())


def profit_factor(trade_pnls: pd.Series) -> float:
    gains = trade_pnls[trade_pnls > 0].sum()
    losses = -trade_pnls[trade_pnls < 0].sum()
    if losses == 0:
        return float(gains > 0) * 99.0  # cota para evitar infinito
    return float(gains / losses)


def win_rate(trade_pnls: pd.Series) -> float:
    return float((trade_pnls > 0).mean()) if len(trade_pnls) else 0.0


def summarize(returns: pd.Series, equity: pd.Series, trade_pnls: pd.Series,
              periods_per_year: int = 252) -> dict:
    return {
        "sharpe": sharpe_ratio(returns, periods_per_year),
        "profit_factor": profit_factor(trade_pnls),
        "max_drawdown": max_drawdown(equity),
        "win_rate": win_rate(trade_pnls),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1) if len(equity) else 0.0,
        "num_trades": int(len(trade_pnls)),
    }
