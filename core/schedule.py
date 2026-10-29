"""Calendario de la ejecución diaria (barras ``1Day``).

Convención (igual que el backtest): la señal se calcula con la última barra diaria
**cerrada** (el cierre de ayer) y la orden de mercado se envía poco después de la
apertura (``daily_run_time``, por defecto 09:35 ET), es decir, "señal al cierre,
ejecución en la apertura siguiente". La barra del día en curso (incompleta) se descarta.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime

import pandas as pd

from data.base import NY

DEFAULT_DAILY_RUN_TIME = "09:35"
HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DAILY_BAR_FINAL = dtime(16, 15)       # a partir de aquí la barra diaria de hoy se considera cerrada
REGULAR_CLOSE = dtime(16, 0)
MIN_DAILY_LOOKBACK_DAYS = 420         # ~290 sesiones: SMA200 + calentamiento de las EMAs/RSI


def parse_hhmm(value: str) -> dtime:
    m = HHMM_RE.match(str(value).strip())
    if not m:
        raise ValueError(f"hora inválida: {value!r} (formato HH:MM, hora de Nueva York)")
    return dtime(int(m.group(1)), int(m.group(2)))


def ny_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(NY)


def completed_daily_bars(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Quita la barra diaria del día en curso (y posteriores) mientras no esté cerrada."""
    if df is None or df.empty:
        return df
    et = ny_now(now)
    days = pd.DatetimeIndex(df.index).tz_convert(NY).date
    today = et.date()
    keep = days < today
    if et.time() >= DAILY_BAR_FINAL:
        keep = keep | (days == today)
    return df[keep]


def effective_lookback(cfg: dict) -> int:
    """En diario se garantizan suficientes barras (con 30 días la señal sería siempre "hold")."""
    days = int(cfg.get("lookback_days", 30))
    return max(days, MIN_DAILY_LOOKBACK_DAYS) if cfg.get("timeframe") == "1Day" else days


def trading_session(provider, day: date) -> tuple[datetime, datetime] | None:
    """(apertura, cierre) de ``day`` en NY, o None si no es día hábil.

    Usa el calendario del proveedor si lo tiene (Alpaca: festivos y cierres anticipados);
    si no, lunes a viernes 09:30-16:00 ET.
    """
    sessions = getattr(provider, "sessions", None)
    if callable(sessions):
        try:
            start = datetime.combine(day, dtime(0), NY)
            got = sessions(start, start + timedelta(hours=23))
            if isinstance(got, dict):
                return got.get(day)
        except Exception:  # noqa: BLE001 - sin calendario: horario estándar
            pass
    if day.weekday() >= 5:
        return None
    return (datetime.combine(day, dtime(9, 30), NY), datetime.combine(day, REGULAR_CLOSE, NY))


class DailyGate:
    """Deja pasar una ejecución por día hábil a partir de ``run_time`` (hora de NY).

    ``until_close=True`` (órdenes) además exige que el mercado siga abierto (antes del cierre
    de la sesión menos 5 min): si el bot se arranca tarde, ese día no opera.
    """

    def __init__(self, until_close: bool = False) -> None:
        self.until_close = until_close
        self.last_day: date | None = None

    def due(self, now: datetime, run_time: str, provider=None) -> bool:
        et = ny_now(now)
        if self.last_day == et.date() or et.time() < parse_hhmm(run_time):
            return False
        session = trading_session(provider, et.date())
        if session is None:
            return False
        if self.until_close and et >= session[1] - timedelta(minutes=5):
            return False
        return True

    def mark(self, now: datetime) -> None:
        self.last_day = ny_now(now).date()
