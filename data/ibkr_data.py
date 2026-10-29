"""Datos históricos de Interactive Brokers (reqHistoricalData) para bot, backtesting y Optuna.

IBKR limita el volumen de cada petición y aplica *pacing* (máx. ~60 peticiones cada
10 min), así que los rangos largos se trocean hacia atrás en bloques de tamaño
seguro para cada barra y se espera ``IBKR_HIST_PACING_SEC`` entre bloques.

Barras diarias de acciones: se piden **ajustadas** por splits y dividendos
(``whatToShow="ADJUSTED_LAST"``, ``IBKR_ADJUSTED_DAILY=true`` por defecto). IBKR exige
``endDateTime=""`` (hasta hoy) para ese tipo, así que se hace una única petición en años
(``"N Y"``) y se recorta al rango pedido.
"""
from __future__ import annotations

import math
import time
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from connectors.ibkr import IBKRConnection, get_shared_connection
from data.base import NY, OHLCV_COLUMNS, empty_bars, env_bool, is_crypto

# timeframe de la plataforma -> (barSizeSetting de IBKR, días máximos por petición)
BAR_SIZES = {
    "1Min": ("1 min", 5),
    "5Min": ("5 mins", 20),
    "15Min": ("15 mins", 50),
    "1Hour": ("1 hour", 50),
    "1Day": ("1 day", 365),
}


def _bar_timestamp(value) -> pd.Timestamp:
    """Las barras diarias llegan como ``date``: se fechan a medianoche de Nueva York (como Alpaca)."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return pd.Timestamp(value).tz_localize(NY).tz_convert(UTC)
    return pd.Timestamp(value)


def bars_to_df(bars) -> pd.DataFrame:
    """Convierte una lista de ``BarData`` de ib_async en DataFrame OHLCV (índice UTC)."""
    rows = [
        {"timestamp": _bar_timestamp(b.date), "open": b.open, "high": b.high, "low": b.low,
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
                 sleep=time.sleep, adjusted_daily: bool | None = None) -> None:
        self.conn = connection or get_shared_connection()
        self.default_mode = mode or "paper"
        self._sleep = sleep
        self.adjusted_daily = (env_bool("IBKR_ADJUSTED_DAILY", True) if adjusted_daily is None
                               else bool(adjusted_daily))

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

        if timeframe == "1Day" and self.adjusted_daily and not is_crypto(symbol):
            return self._adjusted_daily(ib, contract, start, end, use_rth)

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

    def _adjusted_daily(self, ib, contract, start: datetime, end: datetime, use_rth: bool) -> pd.DataFrame:
        """Diario ajustado: una petición ``ADJUSTED_LAST`` desde ``start`` hasta hoy, recortada a ``end``."""
        span_days = max((datetime.now(UTC) - start).days, 1)
        years = max(1, math.ceil(span_days / 365))
        bars = self.conn.run(
            ib.reqHistoricalDataAsync, contract, endDateTime="",
            durationStr=f"{years} Y", barSizeSetting="1 day", whatToShow="ADJUSTED_LAST",
            useRTH=use_rth, formatDate=2,
            timeout=self.conn.config.request_timeout,
        )
        df = bars_to_df(bars)
        if df.empty:
            return df
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
