"""Ingesta de datos históricos desde Alpaca (acciones y cripto) con alpaca-py."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pandas as pd

from data.base import (
    NY,
    OHLCV_COLUMNS,
    DataProviderError,
    default_sessions,
    empty_bars,
    env_bool,
    is_crypto,
    regular_session_bars,
)
from monitoring.logger import get_logger

log = get_logger("data.alpaca")

__all__ = ["ADJUSTMENTS", "AlpacaDataProvider", "is_crypto", "normalize_bars"]

# Ajuste de precios de acciones (alpaca-py ``Adjustment``). Por defecto "all" (splits + dividendos):
# sin ajuste un split aparece como un desplome (p.ej. NVDA -90 % el 2024-06-10) y rompe señales y backtests.
ADJUSTMENTS = ("raw", "split", "dividend", "all")
DEFAULT_ADJUSTMENT = "all"


def adjustment_from_env() -> str:
    """``ALPACA_ADJUSTMENT`` (raw | split | dividend | all); valor inválido -> "all" con WARNING."""
    raw = os.getenv("ALPACA_ADJUSTMENT", "").strip().lower()
    if not raw:
        return DEFAULT_ADJUSTMENT
    if raw not in ADJUSTMENTS:
        log.warning("ALPACA_ADJUSTMENT inválido; se usa 'all'", extra={"value": raw})
        return DEFAULT_ADJUSTMENT
    return raw

TIMEFRAME_MAP = {
    "1Min": (1, "Minute"),
    "30Min": (30, "Minute"),   # interno: base para las barras horarias alineadas a las 09:30
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
                 stock_client=None, crypto_client=None, calendar_client=None,
                 regular_hours_only: bool | None = None, adjustment: str | None = None) -> None:
        self.api_key = api_key or os.getenv("APCA_API_KEY_ID")
        self.secret_key = secret_key or os.getenv("APCA_API_SECRET_KEY")
        self._stock = stock_client
        self._crypto = crypto_client
        self._calendar = calendar_client
        # Por defecto solo sesión regular (como useRTH de IBKR); ALPACA_REGULAR_HOURS_ONLY=false lo desactiva.
        self.regular_hours_only = (env_bool("ALPACA_REGULAR_HOURS_ONLY", True)
                                   if regular_hours_only is None else bool(regular_hours_only))
        self._calendar_warned = False
        adjustment = adjustment_from_env() if adjustment is None else str(adjustment).lower()
        if adjustment not in ADJUSTMENTS:
            raise ValueError(f"adjustment inválido: {adjustment} (admitidos: {', '.join(ADJUSTMENTS)})")
        self.adjustment = adjustment

    @property
    def stock_client(self):
        if self._stock is None:
            from alpaca.data.historical import StockHistoricalDataClient

            if not (self.api_key and self.secret_key):
                raise DataProviderError(
                    "Los datos de acciones de Alpaca requieren APCA_API_KEY_ID y APCA_API_SECRET_KEY "
                    "(los de cripto son públicos)")
            self._stock = StockHistoricalDataClient(self.api_key, self.secret_key)
        return self._stock

    @property
    def crypto_client(self):
        if self._crypto is None:
            from alpaca.data.historical import CryptoHistoricalDataClient

            # Los datos cripto no requieren claves, pero se usan si existen.
            self._crypto = CryptoHistoricalDataClient(self.api_key, self.secret_key)
        return self._crypto

    @property
    def calendar_client(self):
        if self._calendar is None:
            from alpaca.trading.client import TradingClient

            # El calendario es el mismo en paper y live; solo lectura.
            self._calendar = TradingClient(self.api_key, self.secret_key, paper=True)
        return self._calendar

    def sessions(self, start: datetime, end: datetime) -> dict:
        """Sesiones regulares del calendario de Alpaca (festivos y cierres anticipados incluidos).

        Si el calendario no está disponible se usa 09:30–16:00 ET de lunes a viernes (un WARNING).
        """
        try:
            from alpaca.trading.requests import GetCalendarRequest

            days = self.calendar_client.get_calendar(GetCalendarRequest(
                start=start.astimezone(NY).date(), end=end.astimezone(NY).date()))
            out = {}
            for d in days:
                o, c = pd.Timestamp(d.open), pd.Timestamp(d.close)
                out[d.date] = (o.tz_localize(NY) if o.tzinfo is None else o.tz_convert(NY),
                               c.tz_localize(NY) if c.tzinfo is None else c.tz_convert(NY))
            return out
        except Exception as exc:  # noqa: BLE001 - sin calendario se usa el horario estándar
            if not self._calendar_warned:
                log.warning("calendario de Alpaca no disponible; se usa 09:30-16:00 ET",
                            extra={"error": str(exc) or type(exc).__name__})
                self._calendar_warned = True
            return default_sessions(start, end)

    def get_bars(self, symbol: str, timeframe: str = "1Hour", days: int = 30,
                 end: datetime | None = None, regular_hours: bool | None = None) -> pd.DataFrame:
        """Devuelve un DataFrame OHLCV indexado por timestamp UTC (inicio de la barra).

        En acciones intradía, por defecto solo la sesión regular (``regular_hours``/
        ``ALPACA_REGULAR_HOURS_ONLY``); las barras de 1 hora se alinean a la apertura (09:30 ET).
        Los precios de acciones se piden ajustados (``adjustment``, por defecto splits + dividendos).
        """
        end = end or datetime.now(UTC) - timedelta(minutes=16)  # evita restricción SIP
        start = end - timedelta(days=days)
        if is_crypto(symbol):
            from alpaca.data.requests import CryptoBarsRequest

            req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=_timeframe(timeframe),
                                    start=start, end=end)
            return normalize_bars(self.crypto_client.get_crypto_bars(req).df, symbol)

        from alpaca.data.enums import Adjustment
        from alpaca.data.requests import StockBarsRequest

        rth = self.regular_hours_only if regular_hours is None else bool(regular_hours)
        intraday = timeframe != "1Day"
        fetch_tf = "30Min" if (rth and timeframe == "1Hour") else timeframe
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=_timeframe(fetch_tf), start=start, end=end,
                               adjustment=Adjustment(self.adjustment))
        df = normalize_bars(self.stock_client.get_stock_bars(req).df, symbol)
        if rth and intraday and not df.empty:
            df = regular_session_bars(df, timeframe, self.sessions(start, end))
        return df


def normalize_bars(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Quita el MultiIndex (symbol, timestamp) que devuelve alpaca-py."""
    if df is None or df.empty:
        return empty_bars()
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level="symbol") if symbol in df.index.get_level_values(0) else df.droplevel(0)
    df = df[OHLCV_COLUMNS].astype(float).sort_index()
    return df
