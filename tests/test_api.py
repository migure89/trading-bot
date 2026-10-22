import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from execution.broker import AlpacaBroker
from execution.factory import BrokerRegistry
from execution.ibkr_broker import IBKRBroker


@pytest.fixture
def ibkr_broker(ibkr_conn):
    return IBKRBroker(ibkr_conn)


@pytest.fixture
def client(store, fake_trading_client, ibkr_broker):
    alpaca = AlpacaBroker(client_factory=lambda paper: fake_trading_client)
    registry = BrokerRegistry({"alpaca": lambda: alpaca, "ibkr": lambda: ibkr_broker})
    api_main.app.dependency_overrides[api_main.get_store] = lambda: store
    api_main.app.dependency_overrides[api_main.get_brokers] = lambda: registry
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


def test_status(client):
    r = client.get("/status").json()
    assert r["mode"] == "paper" and r["broker"] == "alpaca"
    assert r["account"]["equity"] == 100000
    assert r["connection"]["connected"] is True


def test_config_update_and_live_block(client):
    assert client.post("/config", json={"risk_pct": 2}).json()["risk_pct"] == 2
    assert client.post("/config", json={"mode": "live"}).status_code == 403
    assert client.post("/config", json={"mode": "foo"}).status_code == 422
    assert client.post("/config", json={"broker": "binance"}).status_code == 422


def test_toggle_and_kill_switch(client, fake_trading_client):
    assert client.post("/toggle-bot", json={}).json()["bot_enabled"] is True
    r = client.post("/toggle-bot", json={"enabled": False, "close_positions": True}).json()
    assert r == {"bot_enabled": False, "positions_closed": True}
    fake_trading_client.close_all_positions.assert_called_once_with(cancel_orders=True)


def test_metrics(client, store):
    store.record_trade("AAPL", "sell", 1, 100, "paper", pnl=5.0, broker="alpaca")
    r = client.get("/metrics").json()
    assert r["num_trades"] == 1 and r["win_rate"] == 1.0
    assert r["trades"][0]["broker"] == "alpaca"


def test_switch_to_ibkr_hot(client, fake_ib):
    assert client.get("/brokers").json()["connections"]["ibkr"]["initialized"] is False
    assert client.post("/config", json={"broker": "ibkr"}).json()["broker"] == "ibkr"
    r = client.get("/status").json()
    assert r["broker"] == "ibkr" and r["data_provider"] == "ibkr"
    assert r["account"]["equity"] == 100000 and r["account"]["daily_pnl"] == 500
    assert r["connection"]["connected"] is True and r["connection"]["port"] == 4002
    info = client.get("/brokers").json()
    assert info["active"] == "ibkr" and info["connections"]["ibkr"]["account"] == "DU1234567"


def test_ibkr_kill_switch_and_cancel(client, fake_ib):
    from tests.conftest import ib_portfolio_item

    fake_ib.portfolio_items = [ib_portfolio_item("AAPL", 10)]
    client.post("/config", json={"broker": "ibkr"})
    assert client.post("/cancel-orders").json() == {"cancelled": True, "broker": "ibkr"}
    r = client.post("/toggle-bot", json={"enabled": False, "close_positions": True}).json()
    assert r["positions_closed"] is True
    assert fake_ib.global_cancels == 2
    contract, order = fake_ib.orders[-1]
    assert order.action == "SELL" and order.totalQuantity == 10 and contract.conId == 42


def test_ibkr_unavailable_returns_503(client, fake_ib):
    fake_ib.fail_connect = True
    client.post("/config", json={"broker": "ibkr"})
    r = client.get("/status").json()
    assert "No se pudo conectar" in r["error"] and r["connection"]["connected"] is False
    assert client.post("/cancel-orders").status_code == 503


def test_broker_switch_in_live_needs_confirmation(client, monkeypatch):
    monkeypatch.setenv("CONFIRM_LIVE_TRADING", "true")
    assert client.post("/config", json={"mode": "live"}).status_code == 200
    assert client.post("/config", json={"broker": "ibkr"}).status_code == 409
    r = client.post("/config", json={"broker": "ibkr", "confirm_live": True})
    assert r.status_code == 200 and r.json()["broker"] == "ibkr"
