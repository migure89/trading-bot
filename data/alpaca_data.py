"""Ingesta de datos históricos desde Alpaca (acciones y cripto) con alpaca-py."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pandas as pd

from data.base import OHLCV_COLUMNS, empty_bars, is_crypto

__all__ = ["AlpacaDataProvider", "is_crypto", "normalize_bars"]

TIMEFRAME_MAP = {
    "1Min": (1, "Minute"),
    "5Min": (5, "Minute"),
    "15Min": (15, "Minute"),
    "1Hour": (1, "Hour"),
    "1Day": (1, "Day"),
}


def _timeframe(tf: str):
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    amount, unit = TIMEFRAME_MAP.get(tf, (1, "Hour"))
    return TimeFrame(amount, getattr(TimeFrameUnit, unit))


class AlpacaDataProvider:
    """Envuelve StockHistoricalDataClient y CryptoHistoricalDataClient."""

    name = "alpaca"

    def __init__(self, api_key: str | None = None, secret_key: str | None = None,
                 stock_client=None, crypto_client=None) -> None:
        self.api_key = api_key or os.getenv("APCA_API_KEY_ID")
        self.secret_key = secret_key or os.getenv("APCA_API_SECRET_KEY")
        self._stock = stock_client
        self._crypto = crypto_client

    @property
    def stock_client(self):
        if self._stock is None:
            from alpaca.data.historical import StockHistoricalDataClient

            self._stock = StockHistoricalDataClient(self.api_key, self.secret_key)
        return self._stock

    @property
    def crypto_client(self):
        if self._crypto is None:
            from alpaca.data.historical import CryptoHistoricalDataClient

            # Los datos cripto no requieren claves, pero se usan si existen.
            self._crypto = CryptoHistoricalDataClient(self.api_key, self.secret_key)
        return self._crypto

    def get_bars(self, symbol: str, timeframe: str = "1Hour", days: int = 30,
                 end: datetime | None = None) -> pd.DataFrame:
        """Devuelve un DataFrame OHLCV indexado por timestamp."""
        end = end or datetime.now(UTC) - timedelta(minutes=16)  # evita restricción SIP
        start = end - timedelta(days=days)
        tf = _timeframe(timeframe)
        if is_crypto(symbol):
            from alpaca.data.requests import CryptoBarsRequest

            req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=tf, start=start, end=end)
            bars = self.crypto_client.get_crypto_bars(req)
        else:
            from alpaca.data.requests import StockBarsRequest

            req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf, start=start, end=end)
            bars = self.stock_client.get_stock_bars(req)
        return normalize_bars(bars.df, symbol)


def normalize_bars(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Quita el MultiIndex (symbol, timestamp) que devuelve alpaca-py."""
    if df is None or df.empty:
        return empty_bars()
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level="symbol") if symbol in df.index.get_level_values(0) else df.droplevel(0)
    df = df[OHLCV_COLUMNS].astype(float).sort_index()
    return df
