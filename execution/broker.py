"""Cliente de órdenes sobre TradingClient de alpaca-py con modo paper/live dinámico."""
from __future__ import annotations

import os

from config.settings import LiveTradingNotConfirmed, live_trading_confirmed
from data.base import is_crypto
from execution.base import BaseBroker, BrokerConnectionError


def alpaca_credentials() -> tuple[str, str] | None:
    """Claves de Alpaca del entorno, o ``None`` si falta alguna."""
    key, secret = os.getenv("APCA_API_KEY_ID", "").strip(), os.getenv("APCA_API_SECRET_KEY", "").strip()
    return (key, secret) if key and secret else None


class AlpacaBroker(BaseBroker):
    """Recrea el TradingClient cuando cambia el modo en la configuración."""

    name = "alpaca"

    def __init__(self, client_factory=None) -> None:
        self._factory = client_factory or self._default_factory
        self._client = None
        self.mode: str | None = None
        self._last_ok = False
        self.last_error: str | None = None

    @staticmethod
    def _default_factory(paper: bool):
        from alpaca.trading.client import TradingClient

        creds = alpaca_credentials()
        if creds is None:
            raise BrokerConnectionError(
                "Alpaca no está configurado: faltan APCA_API_KEY_ID y/o APCA_API_SECRET_KEY en el entorno")
        return TradingClient(*creds, paper=paper)

    def ensure_mode(self, mode: str):
        if mode == "live" and not live_trading_confirmed():
            raise LiveTradingNotConfirmed("CONFIRM_LIVE_TRADING=true es obligatorio para live")
        if self._client is None or mode != self.mode:
            try:
                client = self._factory(paper=(mode != "live"))
            except BrokerConnectionError as exc:
                self._last_ok, self.last_error = False, str(exc)
                raise
            except Exception as exc:  # p.ej. ValueError de alpaca-py con claves vacías
                self._last_ok = False
                self.last_error = f"No se pudo crear el cliente de Alpaca: {exc}"
                raise BrokerConnectionError(self.last_error) from exc
            self._client, self.mode = client, mode
        return self._client

    @property
    def client(self):
        if self._client is None:
            raise RuntimeError("Llama antes a ensure_mode()")
        return self._client

    # ---- consultas ------------------------------------------------------
    def connection_status(self) -> dict:
        """Alpaca es REST: 'connected' indica que la última llamada a la cuenta funcionó."""
        return {
            "broker": self.name,
            "connected": self._client is not None and self._last_ok,
            "mode": self.mode,
            "credentials": alpaca_credentials() is not None,
            "last_error": self.last_error,
        }

    def account(self) -> dict:
        try:
            a = self.client.get_account()
        except Exception as exc:
            self._last_ok, self.last_error = False, str(exc)
            raise
        self._last_ok, self.last_error = True, None
        equity, last = float(a.equity), float(a.last_equity)
        return {
            "equity": equity,
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "daily_pnl": equity - last,
            "daily_pnl_pct": (equity - last) / last * 100 if last else 0.0,
        }

    def positions(self) -> list[dict]:
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price or 0),
                "market_value": float(p.market_value or 0),
                "unrealized_pl": float(p.unrealized_pl or 0),
            }
            for p in self.client.get_all_positions()
        ]

    # ---- órdenes --------------------------------------------------------
    def market_order(self, symbol: str, qty: float, side: str):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.GTC if is_crypto(symbol) else TimeInForce.DAY,
        )
        return self.client.submit_order(order_data=req)

    def close_position(self, symbol: str):
        return self.client.close_position(symbol.replace("/", ""))

    def cancel_all_orders(self):
        return self.client.cancel_orders()

    def close_all(self):
        """Kill switch: cancela órdenes y liquida todas las posiciones."""
        return self.client.close_all_positions(cancel_orders=True)
