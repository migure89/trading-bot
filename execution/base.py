"""Interfaz común de broker.

Todas las implementaciones (Alpaca, Interactive Brokers…) exponen los mismos métodos
y devuelven diccionarios con el mismo formato, de modo que el bot, la API y el
dashboard no dependen del broker concreto.
"""
from __future__ import annotations

import math
import os
import time
from abc import ABC, abstractmethod
from typing import Any

# Estados normalizados de una orden (comunes a todos los brokers).
FILLED, PARTIAL, PENDING, CANCELED, REJECTED = (
    "filled", "partially_filled", "pending", "canceled", "rejected")
TERMINAL_STATUSES = {FILLED, CANCELED, REJECTED}


def fill_timeout() -> float:
    """Segundos que se espera a que se ejecute una orden a mercado (ORDER_FILL_TIMEOUT)."""
    try:
        return max(0.0, float(os.getenv("ORDER_FILL_TIMEOUT", "10")))
    except ValueError:
        return 10.0


def fill_info(status: str, filled_qty: Any = None, avg_price: Any = None) -> dict:
    """Snapshot normalizado de la ejecución: ``{"status", "filled_qty", "avg_price"}``."""
    def num(v):
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    qty, price = num(filled_qty), num(avg_price)
    # Cancelada/expirada tras ejecutarse en parte: cuenta lo ejecutado.
    if status == CANCELED and qty:
        status = PARTIAL
    return {"status": status, "filled_qty": qty, "avg_price": price if price else None}


class BrokerConnectionError(RuntimeError):
    """El broker no está disponible (sin credenciales, TWS/Gateway caído, etc.)."""


class BaseBroker(ABC):
    """Contrato mínimo que debe cumplir un broker."""

    name: str = "base"
    mode: str | None = None

    @abstractmethod
    def ensure_mode(self, mode: str) -> Any:
        """Prepara el cliente para ``paper`` o ``live`` (live exige CONFIRM_LIVE_TRADING=true)."""

    @abstractmethod
    def account(self) -> dict:
        """``{"equity", "cash", "buying_power", "daily_pnl", "daily_pnl_pct"}``."""

    @abstractmethod
    def positions(self) -> list[dict]:
        """Lista de dicts: symbol, qty, avg_entry_price, current_price, market_value, unrealized_pl."""

    @abstractmethod
    def market_order(self, symbol: str, qty: float, side: str) -> Any:
        """Envía una orden a mercado. El objeto devuelto expone ``id``."""

    @abstractmethod
    def close_position(self, symbol: str) -> Any:
        """Cierra la posición de un símbolo."""

    @abstractmethod
    def cancel_all_orders(self) -> Any:
        """Cancela todas las órdenes abiertas."""

    @abstractmethod
    def close_all(self) -> Any:
        """Kill switch: cancela órdenes y liquida todas las posiciones."""

    def order_fill(self, order: Any) -> dict:
        """Estado actual de ejecución de ``order`` (ver ``fill_info``). Por defecto: desconocido."""
        return fill_info(PENDING)

    def wait_fill(self, order: Any, timeout: float | None = None, poll: float = 0.25) -> dict:
        """Espera (como mucho ``timeout`` s) a que la orden termine y devuelve su ejecución."""
        deadline = time.monotonic() + (fill_timeout() if timeout is None else timeout)
        while True:
            info = self.order_fill(order)
            if info["status"] in TERMINAL_STATUSES or time.monotonic() >= deadline:
                return info
            time.sleep(poll)

    def connection_status(self) -> dict:
        """Estado de conexión para la API/dashboard."""
        return {"broker": self.name, "connected": False, "mode": self.mode}

    # ---- utilidades comunes ---------------------------------------------
    def position(self, symbol: str) -> dict | None:
        """Posición abierta de ``symbol`` (acepta "BTC/USD" y "BTCUSD") o ``None``."""
        key = symbol.replace("/", "")
        for p in self.positions():
            if p["symbol"] in (symbol, key):
                return p
        return None

    def position_qty(self, symbol: str) -> float:
        pos = self.position(symbol)
        return pos["qty"] if pos else 0.0

    @staticmethod
    def position_size(equity: float, risk_pct: float, price: float, atr_value: float,
                      atr_mult: float, crypto: bool = False) -> float:
        """Tamaño por riesgo: (equity*riesgo%) / (ATR*mult), limitado al equity."""
        if price <= 0 or atr_value <= 0:
            return 0.0
        risk_cash = equity * risk_pct / 100
        qty = min(risk_cash / (atr_value * atr_mult), equity / price)
        return round(qty, 6) if crypto else float(math.floor(qty))
