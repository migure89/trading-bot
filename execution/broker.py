"""Cliente de órdenes sobre TradingClient de alpaca-py con modo paper/live dinámico."""
from __future__ import annotations

import math
import os

from config.settings import LiveTradingNotConfirmed, live_trading_confirmed
from data.alpaca_data import is_crypto


class AlpacaBroker:
    """Recrea el TradingClient cuando cambia el modo en la configuración."""

    def __init__(self, client_factory=None) -> None:
        self._factory = client_factory or self._default_factory
        self._client = None
        self.mode: str | None = None

    @staticmethod
    def _default_factory(paper: bool):
        from alpaca.trading.client import TradingClient

        return TradingClient(
            os.getenv("APCA_API_KEY_ID"), os.getenv("APCA_API_SECRET_KEY"), paper=paper
        )

    def ensure_mode(self, mode: str):
        if mode == "live" and not live_trading_confirmed():
            raise LiveTradingNotConfirmed("CONFIRM_LIVE_TRADING=true es obligatorio para live")
        if self._client is None or mode != self.mode:
            self._client = self._factory(paper=(mode != "live"))
            self.mode = mode
        return self._client

    @property
    def client(self):
        if self._client is None:
            raise RuntimeError("Llama antes a ensure_mode()")
        return self._client

    # ---- consultas ------------------------------------------------------
    def account(self) -> dict:
        a = self.client.get_account()
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

    def position_qty(self, symbol: str) -> float:
        key = symbol.replace("/", "")
        for p in self.positions():
            if p["symbol"] in (symbol, key):
                return p["qty"]
        return 0.0

    # ---- órdenes --------------------------------------------------------
    @staticmethod
    def position_size(equity: float, risk_pct: float, price: float, atr_value: float,
                      atr_mult: float, crypto: bool = False) -> float:
        """Tamaño por riesgo: (equity*riesgo%) / (ATR*mult), limitado al equity."""
        if price <= 0 or atr_value <= 0:
            return 0.0
        risk_cash = equity * risk_pct / 100
        qty = min(risk_cash / (atr_value * atr_mult), equity / price)
        return round(qty, 6) if crypto else float(math.floor(qty))

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

    def close_all(self):
        """Kill switch: cancela órdenes y liquida todas las posiciones."""
        return self.client.close_all_positions(cancel_orders=True)
