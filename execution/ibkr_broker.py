"""Broker Interactive Brokers sobre ``ib_async`` (TWS / IB Gateway).

Implementa la misma interfaz que ``AlpacaBroker``: modo paper/live según puerto,
cuenta, posiciones, órdenes a mercado, cancelación y kill switch.
"""
from __future__ import annotations

from types import SimpleNamespace

from config.settings import LiveTradingNotConfirmed, live_trading_confirmed
from connectors.ibkr import IBKRConnection, get_shared_connection
from data.base import is_crypto
from execution.base import BaseBroker

# Etiquetas de accountSummary que usamos
_TAGS = {"NetLiquidation": "equity", "TotalCashValue": "cash", "BuyingPower": "buying_power"}


def position_symbol(contract) -> str:
    """Símbolo en formato de la plataforma: 'AAPL' o 'BTC/USD'."""
    if getattr(contract, "secType", "") == "CRYPTO":
        return f"{contract.symbol}/{contract.currency}"
    return contract.symbol or getattr(contract, "localSymbol", "")


class IBKRBroker(BaseBroker):
    name = "ibkr"

    def __init__(self, connection: IBKRConnection | None = None) -> None:
        self.conn = connection or get_shared_connection()
        self.mode: str | None = None

    # ---- conexión --------------------------------------------------------
    def ensure_mode(self, mode: str):
        if mode == "live" and not live_trading_confirmed():
            raise LiveTradingNotConfirmed("CONFIRM_LIVE_TRADING=true es obligatorio para live (IBKR)")
        ib = self.conn.ensure_connected(mode)
        self.mode = mode
        return ib

    @property
    def ib(self):
        if self.mode is None:
            raise RuntimeError("Llama antes a ensure_mode()")
        return self.conn.ib

    def connection_status(self) -> dict:
        return self.conn.status()

    # ---- consultas -------------------------------------------------------
    def account(self) -> dict:
        values = self.conn.run(self.ib.accountSummaryAsync, self.conn.account or "")
        out = {"equity": 0.0, "cash": 0.0, "buying_power": 0.0}
        seen: set[str] = set()
        for v in values or []:
            key = _TAGS.get(v.tag)
            if key is None or v.currency not in ("USD", "BASE", ""):
                continue
            if v.currency == "BASE" or key not in seen:  # BASE tiene prioridad
                out[key] = float(v.value)
                seen.add(key)
        daily = self.conn.daily_pnl() or 0.0
        prev = out["equity"] - daily
        out["daily_pnl"] = daily
        out["daily_pnl_pct"] = daily / prev * 100 if prev else 0.0
        return out

    def _portfolio(self):
        return [p for p in self.conn.run(self.ib.portfolio, self.conn.account or "") or []
                if float(p.position) != 0]

    def positions(self) -> list[dict]:
        out = []
        for p in self._portfolio():
            qty = float(p.position)
            mult = float(getattr(p.contract, "multiplier", "") or 1)
            out.append({
                "symbol": position_symbol(p.contract),
                "qty": qty,
                "avg_entry_price": float(p.averageCost) / mult,
                "current_price": float(p.marketPrice or 0),
                "market_value": float(p.marketValue or 0),
                "unrealized_pl": float(p.unrealizedPNL or 0),
            })
        return out

    # ---- órdenes ---------------------------------------------------------
    def _order(self, action: str, qty: float, crypto: bool):
        from ib_async import MarketOrder

        # Las órdenes cripto en IBKR (PAXOS) solo admiten IOC/Minutes; acciones: DAY.
        order = MarketOrder(action, qty, tif="IOC" if crypto else "DAY")
        if self.conn.account:
            order.account = self.conn.account
        return order

    def _place(self, contract, order):
        trade = self.conn.run(self.ib.placeOrder, contract, order)
        return SimpleNamespace(id=str(trade.order.orderId),
                               status=getattr(trade.orderStatus, "status", None), trade=trade)

    def market_order(self, symbol: str, qty: float, side: str):
        contract = self.conn.qualify(symbol)
        order = self._order("BUY" if side == "buy" else "SELL", abs(qty), is_crypto(symbol))
        return self._place(contract, order)

    def _close_item(self, item):
        from ib_async import Contract

        crypto = item.contract.secType == "CRYPTO"
        exchange = self.conn.config.crypto_exchange if crypto else self.conn.config.stock_exchange
        contract = Contract(conId=item.contract.conId, exchange=exchange)
        qty = float(item.position)
        return self._place(contract, self._order("SELL" if qty > 0 else "BUY", abs(qty), crypto))

    def close_position(self, symbol: str):
        key = symbol.replace("/", "")
        for item in self._portfolio():
            sym = position_symbol(item.contract)
            if sym in (symbol, key) or sym.replace("/", "") == key:
                return self._close_item(item)
        return None

    def cancel_all_orders(self):
        """reqGlobalCancel: cancela TODAS las órdenes abiertas de la sesión (también las manuales)."""
        return self.conn.run(self.ib.reqGlobalCancel)

    def close_all(self):
        """Kill switch: cancela órdenes y liquida todas las posiciones a mercado."""
        self.cancel_all_orders()
        return [self._close_item(item) for item in self._portfolio()]
