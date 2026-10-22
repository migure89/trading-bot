"""Interfaz común de proveedor de datos históricos."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

# Timeframes admitidos en la configuración (comunes a todos los proveedores).
TIMEFRAMES = ("1Min", "5Min", "15Min", "1Hour", "1Day")


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
                 end: datetime | None = None) -> pd.DataFrame: ...
