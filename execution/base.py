"""Interfaz común de broker.

Todas las implementaciones (Alpaca, Interactive Brokers…) exponen los mismos métodos
y devuelven diccionarios con el mismo formato, de modo que el bot, la API y el
dashboard no dependen del broker concreto.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any


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

    def connection_status(self) -> dict:
        """Estado de conexión para la API/dashboard."""
        return {"broker": self.name, "connected": False, "mode": self.mode}

    # ---- utilidades comunes ---------------------------------------------
    def position_qty(self, symbol: str) -> float:
        key = symbol.replace("/", "")
        for p in self.positions():
            if p["symbol"] in (symbol, key):
                return p["qty"]
        return 0.0

    @staticmethod
    def position_size(equity: float, risk_pct: float, price: float, atr_value: float,
                      atr_mult: float, crypto: bool = False) -> float:
        """Tamaño por riesgo: (equity*riesgo%) / (ATR*mult), limitado al equity."""
        if price <= 0 or atr_value <= 0:
            return 0.0
        risk_cash = equity * risk_pct / 100
        qty = min(risk_cash / (atr_value * atr_mult), equity / price)
        return round(qty, 6) if crypto else float(math.floor(qty))
