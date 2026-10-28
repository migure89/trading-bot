"""Fixtures comunes: datos sintéticos y mocks de Alpaca (sin credenciales)."""
# ruff: noqa: E402
from __future__ import annotations

import os

# Los tests no deben depender del .env local del desarrollador (claves, API_TOKEN, rutas…).
os.environ["SKIP_DOTENV"] = "1"

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from config.settings import SettingsStore


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    n = 600
    t = np.arange(n)
    close = 100 + 10 * np.sin(t / 25) + np.cumsum(rng.normal(0, 0.5, n))
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "open": close + rng.normal(0, 0.2, n),
        "high": close + abs(rng.normal(0, 0.8, n)),
        "low": close - abs(rng.normal(0, 0.8, n)),
        "close": close,
        "volume": rng.integers(1000, 5000, n).astype(float),
    }, index=idx)


@pytest.fixture
def store(tmp_path, monkeypatch) -> SettingsStore:
    monkeypatch.delenv("CONFIRM_LIVE_TRADING", raising=False)
    return SettingsStore(tmp_path / "settings.db")


@pytest.fixture
def fake_trading_client():
    client = MagicMock()
    client.get_account.return_value = SimpleNamespace(
        equity="100000", last_equity="99000", cash="50000", buying_power="100000")
    client.get_all_positions.return_value = []
    client.submit_order.return_value = SimpleNamespace(id="order-1")
    # Orden ya ejecutada al consultarla (precio/cantidad reales opcionales)
    client.get_order_by_id.side_effect = lambda oid: SimpleNamespace(
        id=oid, status="filled", filled_qty=None, filled_avg_price=None)
    return client


@pytest.fixture
def provider(ohlcv):
    p = MagicMock()
    p.get_bars.return_value = ohlcv
    return p


# ---------------------------------------------------------------------------
# Interactive Brokers: IB falso (sin TWS/Gateway ni red)
# ---------------------------------------------------------------------------
class FakeIB:
    """Imita la superficie de ``ib_async.IB`` que usa la plataforma."""

    def __init__(self, accounts=("DU1234567",), bars=None, portfolio=None, fail_connect=False):
        self.connected = False
        self.accounts = list(accounts)
        self.bars = bars or []
        self.fill_price = 100.0
        self.portfolio_items = list(portfolio or [])
        self.fail_connect = fail_connect
        self.connect_calls: list[tuple] = []
        self.hist_calls: list[dict] = []
        self.orders: list[tuple] = []
        self.global_cancels = 0
        self.pnl_requests: list[str] = []
        self._next_id = 100

    def isConnected(self):
        return self.connected

    async def connectAsync(self, host, port, clientId=1, timeout=4, readonly=False, account=""):
        self.connect_calls.append((host, port, clientId))
        if self.fail_connect:
            raise ConnectionRefusedError("connection refused")
        self.connected = True
        return self

    def disconnect(self):
        self.connected = False

    def managedAccounts(self):
        return self.accounts

    async def qualifyContractsAsync(self, *contracts):
        for i, c in enumerate(contracts):
            c.conId = 1000 + i
        return list(contracts)

    async def reqHistoricalDataAsync(self, contract, **kw):
        self.hist_calls.append({"contract": contract, **kw})
        return self.bars

    async def accountSummaryAsync(self, account=""):
        from ib_async import AccountValue

        return [
            AccountValue(account, "NetLiquidation", "100000", "USD", ""),
            AccountValue(account, "TotalCashValue", "40000", "USD", ""),
            AccountValue(account, "BuyingPower", "200000", "USD", ""),
            AccountValue(account, "NetLiquidation", "1", "EUR", ""),  # se ignora
        ]

    def reqPnL(self, account, modelCode=""):
        self.pnl_requests.append(account)

    def pnl(self, account="", modelCode=""):
        return [SimpleNamespace(dailyPnL=500.0)]

    def portfolio(self, account=""):
        return self.portfolio_items

    def placeOrder(self, contract, order):
        self._next_id += 1
        order.orderId = self._next_id
        self.orders.append((contract, order))
        status = SimpleNamespace(status="Filled", filled=order.totalQuantity,
                                 avgFillPrice=self.fill_price)
        return SimpleNamespace(order=order, orderStatus=status)

    def reqGlobalCancel(self):
        self.global_cancels += 1


def ib_portfolio_item(symbol="AAPL", qty=10.0, sec_type="STK", currency="USD"):
    from ib_async import Contract, PortfolioItem

    contract = Contract(secType=sec_type, symbol=symbol, currency=currency, conId=42)
    return PortfolioItem(contract, qty, 110.0, 110.0 * qty, 100.0, 10.0 * qty, 0.0, "DU1234567")


@pytest.fixture
def fake_ib():
    return FakeIB()


@pytest.fixture
def ibkr_conn(fake_ib, monkeypatch):
    from connectors.ibkr import IBKRConfig, IBKRConnection

    monkeypatch.delenv("CONFIRM_LIVE_TRADING", raising=False)
    conn = IBKRConnection(IBKRConfig(port_paper=4002, port_live=4001, client_id=7, pacing_sec=0),
                          ib_factory=lambda: fake_ib)
    yield conn
    if conn._loop is not None:
        conn._loop.call_soon_threadsafe(conn._loop.stop)
