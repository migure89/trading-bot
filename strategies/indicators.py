"""Indicadores técnicos implementados con pandas puro.

Se implementan a mano en lugar de pandas-ta porque esta última tiene problemas
de compatibilidad con numpy>=2 / Python 3.12+. Las fórmulas siguen las
definiciones estándar (Wilder para RSI y ATR).
"""
from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, float("nan"))
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(series, fast) - ema(series, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(series: pd.Series, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    mid = sma(series, period)
    dev = series.rolling(period, min_periods=period).std(ddof=0)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": mid + std * dev, "bb_lower": mid - std * dev})


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev = close.shift()
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def add_all(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Añade todos los indicadores al DataFrame OHLCV según ``params``."""
    out = df.copy()
    c = out["close"]
    out["ema_fast"] = ema(c, int(params["ema_fast"]))
    out["ema_slow"] = ema(c, int(params["ema_slow"]))
    out["sma"] = sma(c, int(params.get("bb_period", 20)))
    out["rsi"] = rsi(c, int(params["rsi_period"]))
    out = out.join(macd(c, int(params["ema_fast"]), int(params["ema_slow"])))
    out = out.join(bollinger(c, int(params.get("bb_period", 20)), float(params.get("bb_std", 2.0))))
    out["atr"] = atr(out["high"], out["low"], c, int(params["atr_period"]))
    return out
