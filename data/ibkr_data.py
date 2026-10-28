"""Datos históricos de Interactive Brokers (reqHistoricalData) para bot, backtesting y Optuna.

IBKR limita el volumen de cada petición y aplica *pacing* (máx. ~60 peticiones cada
10 min), así que los rangos largos se trocean hacia atrás en bloques de tamaño
seguro para cada barra y se espera ``IBKR_HIST_PACING_SEC`` entre bloques.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pandas as pd

from connectors.ibkr import IBKRConnection, get_shared_connection
from data.base import OHLCV_COLUMNS, empty_bars, is_crypto

# timeframe de la plataforma -> (barSizeSetting de IBKR, días máximos por petición)
BAR_SIZES = {
    "1Min": ("1 min", 5),
    "5Min": ("5 mins", 20),
    "15Min": ("15 mins", 50),
    "1Hour": ("1 hour", 50),
    "1Day": ("1 day", 365),
}


def bars_to_df(bars) -> pd.DataFrame:
    """Convierte una lista de ``BarData`` de ib_async en DataFrame OHLCV (índice UTC)."""
    rows = [
        {"timestamp": b.date, "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume}
        for b in bars or []
    ]
    if not rows:
        return empty_bars()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp")[OHLCV_COLUMNS].astype(float)
    # IBKR devuelve volumen -1 cuando no aplica (p.ej. MIDPOINT)
    df["volume"] = df["volume"].clip(lower=0)
    return df


def chunk_plan(days: int, max_days: int) -> list[int]:
    """Divide ``days`` en bloques de como mucho ``max_days`` (del más reciente al más antiguo)."""
    days = max(int(days), 1)
    plan = []
    while days > 0:
        plan.append(min(days, max_days))
        days -= plan[-1]
    return plan


class IBKRDataProvider:
    """Proveedor de barras OHLCV contra TWS / IB Gateway."""

    name = "ibkr"

    def __init__(self, connection: IBKRConnection | None = None, mode: str | None = None,
                 sleep=time.sleep) -> None:
        self.conn = connection or get_shared_connection()
        self.default_mode = mode or "paper"
        self._sleep = sleep

    def get_bars(self, symbol: str, timeframe: str = "1Hour", days: int = 30,
                 end: datetime | None = None, regular_hours: bool | None = None) -> pd.DataFrame:
        """``regular_hours`` -> ``useRTH`` (None = ``IBKR_USE_RTH``)."""
        use_rth = self.conn.config.use_rth if regular_hours is None else bool(regular_hours)
        # Reutiliza el modo con el que ya esté conectado el broker; si no, paper.
        ib = self.conn.ensure_connected(self.conn.mode or self.default_mode)
        contract = self.conn.qualify(symbol)
        bar_size, max_days = BAR_SIZES.get(timeframe, BAR_SIZES["1Hour"])
        what = "AGGTRADES" if is_crypto(symbol) else "TRADES"
        end = end or datetime.now(UTC)
        start = end - timedelta(days=days)

        frames, cursor = [], end
        plan = chunk_plan(days, max_days)
        for i, chunk in enumerate(plan):
            bars = self.conn.run(
                ib.reqHistoricalDataAsync, contract, endDateTime=cursor,
                durationStr=f"{chunk} D", barSizeSetting=bar_size, whatToShow=what,
                useRTH=use_rth, formatDate=2,
                timeout=self.conn.config.request_timeout,
            )
            frames.append(bars_to_df(bars))
            cursor -= timedelta(days=chunk)
            if i < len(plan) - 1:
                self._sleep(self.conn.config.pacing_sec)

        frames = [f for f in frames if not f.empty]
        if not frames:
            return empty_bars()
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df[df.index >= pd.Timestamp(start)]
