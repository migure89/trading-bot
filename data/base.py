"""Interfaz común de proveedor de datos históricos."""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


class DataProviderError(RuntimeError):
    """El proveedor de datos no puede servir el símbolo (sin credenciales, caído…)."""

# Timeframes admitidos en la configuración (comunes a todos los proveedores).
TIMEFRAMES = ("1Min", "5Min", "15Min", "1Hour", "1Day")


NY = ZoneInfo("America/New_York")
REGULAR_OPEN, REGULAR_CLOSE = time(9, 30), time(16, 0)   # sesión regular NYSE/Nasdaq (ET)
INTRADAY_STEP = {"1Min": "1min", "5Min": "5min", "15Min": "15min", "1Hour": "1h"}


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "si", "sí"}


def default_sessions(start: datetime, end: datetime) -> dict[date, tuple[pd.Timestamp, pd.Timestamp]]:
    """Sesiones regulares de lunes a viernes 09:30–16:00 ET (sin festivos ni cierres anticipados)."""
    out = {}
    day = start.astimezone(NY).date()
    last = end.astimezone(NY).date()
    while day <= last:
        if day.weekday() < 5:
            out[day] = (pd.Timestamp(datetime.combine(day, REGULAR_OPEN), tz=NY),
                        pd.Timestamp(datetime.combine(day, REGULAR_CLOSE), tz=NY))
        day += timedelta(days=1)
    return out


def regular_session_bars(df: pd.DataFrame, timeframe: str,
                         sessions: dict[date, tuple[pd.Timestamp, pd.Timestamp]]) -> pd.DataFrame:
    """Deja solo las barras (por su inicio) dentro de la sesión regular de su día.

    Para ``1Hour`` reagrega barras más finas en horas alineadas con la apertura (09:30, 10:30…,
    15:30–16:00), como las barras horarias RTH de IBKR. ``df`` debe estar indexado en UTC.
    """
    if df.empty or timeframe not in INTRADAY_STEP:
        return df
    utc = df.index.tz_convert("UTC") if df.index.tz is not None else df.index.tz_localize("UTC")
    days = utc.tz_convert(NY).date
    nat = (pd.NaT, pd.NaT)
    opens = pd.to_datetime([sessions.get(d, nat)[0] for d in days], utc=True)
    closes = pd.to_datetime([sessions.get(d, nat)[1] for d in days], utc=True)
    mask = (~opens.isna()) & (utc >= opens) & (utc < closes)
    out = df[mask]
    if timeframe != "1Hour" or out.empty:
        return out
    step = pd.Timedelta(INTRADAY_STEP[timeframe])
    o = opens[mask]
    buckets = o + ((utc[mask] - o) // step) * step
    agg = out.groupby(buckets).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    agg.index.name = df.index.name
    return agg[OHLCV_COLUMNS]


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS)


def is_crypto(symbol: str) -> bool:
    """Los pares cripto se escriben 'BTC/USD' en toda la plataforma."""
    return "/" in symbol


@runtime_checkable
class DataProvider(Protocol):
    """Cualquier proveedor devuelve un DataFrame OHLCV indexado por timestamp UTC."""

    name: str

    def get_bars(self, symbol: str, timeframe: str = "1Hour", days: int = 30,
                 end: datetime | None = None, regular_hours: bool | None = None) -> pd.DataFrame:
        """``regular_hours``: solo sesión regular en acciones (None = valor por defecto del proveedor)."""
        ...
