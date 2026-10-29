"""Motor de backtesting vectorizado con pandas/numpy."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting.metrics import infer_periods_per_year, summarize
from strategies.ema_rsi import generate_signals


@dataclass
class BacktestResult:
    data: pd.DataFrame
    equity: pd.Series
    trades: pd.DataFrame
    metrics: dict


def run_backtest(df: pd.DataFrame, params: dict, initial_cash: float = 100_000.0,
                 fee_bps: float = 5.0, timeframe: str = "1Hour",
                 periods_per_year: int | None = None) -> BacktestResult:
    """Simula la estrategia barra a barra de forma vectorizada.

    La posición se desplaza una barra (se opera al cierre de la barra de señal,
    capturando el retorno de la siguiente) para evitar look-ahead bias. El Sharpe se anualiza con
    ``periods_per_year`` o, por defecto, con las barras por día reales de ``df``.
    """
    data = generate_signals(df, params)
    ret = data["close"].pct_change().fillna(0.0)
    pos = data["position"].shift(1).fillna(0.0)
    turnover = pos.diff().abs().fillna(pos.abs())
    strat_ret = pos * ret - turnover * fee_bps / 10_000
    equity = initial_cash * (1 + strat_ret).cumprod()

    # Reconstrucción de operaciones (entrada -> salida).
    trade_id = (pos.diff().fillna(pos) > 0).cumsum().where(pos > 0)
    trades = []
    for tid, grp in strat_ret[trade_id.notna()].groupby(trade_id.dropna()):
        trades.append({
            "trade": int(tid),
            "entry": grp.index[0],
            "exit": grp.index[-1],
            "return": float((1 + grp).prod() - 1),
        })
    trades_df = pd.DataFrame(trades, columns=["trade", "entry", "exit", "return"])
    metrics = summarize(strat_ret, equity, trades_df["return"],
                        periods_per_year or infer_periods_per_year(df.index, timeframe))
    data["equity"] = equity
    return BacktestResult(data=data, equity=equity, trades=trades_df, metrics=metrics)
