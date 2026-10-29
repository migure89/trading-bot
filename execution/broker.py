"""Cliente de órdenes sobre TradingClient de alpaca-py con modo paper/live dinámico."""
from __future__ import annotations

import os
import time
import uuid

from config.settings import LiveTradingNotConfirmed, live_trading_confirmed
from core.alpaca_http import configure_client, is_transient
from data.base import is_crypto
from execution.base import (
    CANCELED,
    FILLED,
    PARTIAL,
    PENDING,
    REJECTED,
    BaseBroker,
    BrokerConnectionError,
    fill_info,
)
from monitoring.logger import get_logger

log = get_logger("execution.alpaca")

DEFAULT_SUBMIT_ATTEMPTS = 2
SUBMIT_RETRY_WAIT_SEC = 1.0


class OrderStatusUnknown(BrokerConnectionError):
    """El envío de una orden falló de forma ambigua y no se pudo confirmar si existe.

    No se reintenta: reenviar podría duplicar la orden. Hay que revisar la cuenta en Alpaca.
    """


def submit_attempts() -> int:
    """``ALPACA_ORDER_SUBMIT_ATTEMPTS`` (1-5, por defecto 2): envíos como máximo por orden."""
    raw = os.getenv("ALPACA_ORDER_SUBMIT_ATTEMPTS", "").strip()
    try:
        return min(5, max(1, int(raw))) if raw else DEFAULT_SUBMIT_ATTEMPTS
    except ValueError:
        return DEFAULT_SUBMIT_ATTEMPTS


def new_client_order_id() -> str:
    """Id propio de la orden (Alpaca lo exige único por cuenta): permite consultarla tras un timeout."""
    return f"tb-{uuid.uuid4().hex}"


def alpaca_credentials() -> tuple[str, str] | None:
    """Claves de Alpaca del entorno, o ``None`` si falta alguna."""
    key, secret = os.getenv("APCA_API_KEY_ID", "").strip(), os.getenv("APCA_API_SECRET_KEY", "").strip()
    return (key, secret) if key and secret else None


class AlpacaBroker(BaseBroker):
    """Recrea el TradingClient cuando cambia el modo en la configuración."""

    name = "alpaca"

    def __init__(self, client_factory=None, sleep=time.sleep) -> None:
        self._factory = client_factory or self._default_factory
        self._sleep = sleep
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
        return configure_client(TradingClient(*creds, paper=paper))

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
    def market_order(self, symbol: str, qty: float, side: str, client_order_id: str | None = None):
        """Envía una orden a mercado sin riesgo de duplicarla.

        Cada orden lleva un ``client_order_id`` propio. Si el envío falla de forma ambigua
        (timeout, corte de conexión, 429/5xx) se consulta la orden por ese id antes de nada:
        si existe se devuelve; si Alpaca responde 404 se reenvía con el **mismo** id (Alpaca
        rechaza ids repetidos), como mucho ``ALPACA_ORDER_SUBMIT_ATTEMPTS`` veces. Si tampoco
        se puede consultar se lanza ``OrderStatusUnknown`` y no se reintenta.
        Los rechazos claros (4xx: saldo, símbolo, permisos…) se propagan sin reintentar.
        """
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        coid = client_order_id or new_client_order_id()
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.GTC if is_crypto(symbol) else TimeInForce.DAY,
            client_order_id=coid,
        )
        attempts = submit_attempts()
        last_exc: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self.client.submit_order(order_data=req)
            except Exception as exc:
                if not is_transient(exc):
                    raise
                last_exc = exc
                log.warning("envío de orden ambiguo; se consulta por client_order_id",
                            extra={"symbol": symbol, "side": side, "qty": qty, "client_order_id": coid,
                                   "attempt": attempt, "error": str(exc) or type(exc).__name__})
            existing = self.find_order(coid)
            if existing is not None:
                log.info("la orden sí se creó; no se reenvía",
                         extra={"symbol": symbol, "client_order_id": coid,
                                "order_id": str(getattr(existing, "id", ""))})
                return existing
            if attempt < attempts:
                self._sleep(SUBMIT_RETRY_WAIT_SEC * attempt)
        raise BrokerConnectionError(
            f"no se pudo enviar la orden {coid} tras {attempts} intento(s): {last_exc}") from last_exc

    def find_order(self, client_order_id: str):
        """Orden con ese ``client_order_id``, ``None`` si Alpaca responde 404.

        Cualquier otro fallo lanza ``OrderStatusUnknown``: sin confirmación no se reenvía nunca.
        """
        try:
            return self.client.get_order_by_client_id(client_order_id)
        except Exception as exc:
            if getattr(exc, "status_code", None) == 404:
                return None
            log.error("estado de la orden desconocido; no se reenvía",
                      extra={"client_order_id": client_order_id, "error": str(exc) or type(exc).__name__})
            raise OrderStatusUnknown(
                f"no se pudo confirmar la orden {client_order_id} tras un envío fallido: {exc}") from exc

    _STATUS = {"filled": FILLED, "partially_filled": PARTIAL, "rejected": REJECTED,
               "canceled": CANCELED, "expired": CANCELED, "done_for_day": CANCELED,
               "replaced": CANCELED, "suspended": REJECTED}

    def order_fill(self, order) -> dict:
        """Relee la orden en Alpaca: ``filled_qty``, ``filled_avg_price`` y ``status``."""
        try:
            order = self.client.get_order_by_id(order.id)
        except Exception:  # noqa: BLE001 - si falla la consulta usamos lo que devolvió submit
            pass
        raw = getattr(getattr(order, "status", None), "value", getattr(order, "status", None))
        status = self._STATUS.get(str(raw or "").lower(), PENDING)
        return fill_info(status, getattr(order, "filled_qty", None),
                         getattr(order, "filled_avg_price", None))

    def close_position(self, symbol: str):
        return self.client.close_position(symbol.replace("/", ""))

    def cancel_all_orders(self):
        return self.client.cancel_orders()

    def close_all(self):
        """Kill switch: cancela órdenes y liquida todas las posiciones."""
        return self.client.close_all_positions(cancel_orders=True)
