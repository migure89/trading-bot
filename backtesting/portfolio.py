"""Simulador de cartera sobre barras diarias (estrategias ``trend_sma``, ``rsi2``, ``blend``).

Convenciones (sin look-ahead):

- Los pesos objetivo del día ``t`` se deciden con el cierre de ``t`` y se operan en la
  apertura de ``t+1``.
- Precios ajustados (splits + dividendos) -> retornos totales.
- Coste por lado = ``cost_bps`` sobre el nominal operado.
- Solo se opera un símbolo cuando cambia su peso objetivo; el resto deriva con el mercado.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from strategies.portfolio import REGIME_SYMBOL, market_regime
from strategies.registry import portfolio_weights


@dataclass
class PortfolioResult:
    equity: pd.Series
    exposure: pd.Series
    trades: pd.DataFrame
    weights: pd.DataFrame

    @property
    def metrics(self) -> dict:
        return portfolio_metrics(self)


def simulate(opens: pd.DataFrame, closes: pd.DataFrame, target: pd.DataFrame, cost_bps: float = 5.0,
             cash0: float = 100_000.0) -> PortfolioResult:
    """Simula ``target`` (pesos decididos al cierre) operando en la apertura siguiente."""
    idx = closes.index
    tgt = target.reindex(index=idx, columns=closes.columns).fillna(0.0)
    syms = list(closes.columns)
    o, c, t = opens.reindex_like(closes).to_numpy(), closes.to_numpy(), tgt.to_numpy()
    shares = np.zeros(len(syms))
    cash = cash0
    prev_t = np.zeros(len(syms))
    entry_px = np.full(len(syms), np.nan)
    eq, ex, trades = [], [], []
    k = cost_bps / 10_000
    for i in range(len(idx)):
        want = t[i - 1] if i > 0 else np.zeros(len(syms))        # decidido al cierre de ayer
        px = o[i]
        valid = ~np.isnan(px)
        equity_open = cash + np.nansum(shares * np.where(valid, px, 0))
        change = np.abs(want - prev_t) > 1e-12
        for j in np.where(change & valid)[0]:
            cur = shares[j] * px[j]
            dv = want[j] * equity_open - cur
            if abs(dv) < 1e-6 * equity_open:
                continue
            if cur <= 0 < want[j]:
                entry_px[j] = px[j]
            if want[j] == 0 and shares[j] > 0:
                trades.append({"symbol": syms[j], "exit": idx[i],
                               "ret": px[j] / entry_px[j] * (1 - k) / (1 + k) - 1})
                entry_px[j] = np.nan
            shares[j] += dv / px[j]
            cash -= dv + abs(dv) * k
        prev_t = np.where(valid, want, prev_t)
        cv = np.nan_to_num(shares * c[i])
        e = cash + cv.sum()
        eq.append(e)
        ex.append(cv.sum() / e if e else 0.0)
    return PortfolioResult(pd.Series(eq, index=idx), pd.Series(ex, index=idx),
                           pd.DataFrame(trades, columns=["symbol", "exit", "ret"]), tgt)


def portfolio_metrics(res: PortfolioResult) -> dict:
    eq = res.equity
    r = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25 if len(eq) > 1 else 0
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    std = r.std()
    mdd = float((eq / eq.cummax() - 1).min()) if len(eq) else 0.0
    tr = res.trades
    return {"cagr": float(cagr), "sharpe": float(r.mean() / std * np.sqrt(252)) if std > 0 else 0.0,
            "max_drawdown": mdd, "total_return": float(eq.iloc[-1] / eq.iloc[0] - 1) if len(eq) else 0.0,
            "num_trades": len(tr), "win_rate": float((tr["ret"] > 0).mean()) if len(tr) else 0.0,
            "exposure": float(res.exposure.mean()) if len(res.exposure) else 0.0}


def backtest_strategy(bars: dict[str, pd.DataFrame], strategy: str = "blend", cost_bps: float = 5.0,
                      symbols: list[str] | None = None) -> PortfolioResult:
    """Backtest de una estrategia de cartera con barras diarias por símbolo (``{símbolo: OHLCV}``).

    El régimen usa ``SPY`` de ``bars`` (si no está, la estrategia queda fuera de mercado).
    """
    symbols = symbols or list(bars)
    closes = pd.DataFrame({s: bars[s]["close"] for s in symbols}).sort_index()
    opens = pd.DataFrame({s: bars[s]["open"] for s in symbols}).reindex(closes.index)
    spy = bars[REGIME_SYMBOL]["close"] if REGIME_SYMBOL in bars else pd.Series(dtype=float)
    regime = market_regime(spy, closes.index)
    weights = portfolio_weights(strategy, closes, regime)
    return simulate(opens, closes, weights, cost_bps)
