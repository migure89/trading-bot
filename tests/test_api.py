import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from execution.broker import AlpacaBroker


@pytest.fixture
def client(store, fake_trading_client):
    broker = AlpacaBroker(client_factory=lambda paper: fake_trading_client)
    api_main.app.dependency_overrides[api_main.get_store] = lambda: store
    api_main.app.dependency_overrides[api_main.get_broker] = lambda: broker
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


def test_status(client):
    r = client.get("/status").json()
    assert r["mode"] == "paper"
    assert r["account"]["equity"] == 100000


def test_config_update_and_live_block(client):
    assert client.post("/config", json={"risk_pct": 2}).json()["risk_pct"] == 2
    assert client.post("/config", json={"mode": "live"}).status_code == 403
    assert client.post("/config", json={"mode": "foo"}).status_code == 422


def test_toggle_and_kill_switch(client, fake_trading_client):
    assert client.post("/toggle-bot", json={}).json()["bot_enabled"] is True
    r = client.post("/toggle-bot", json={"enabled": False, "close_positions": True}).json()
    assert r == {"bot_enabled": False, "positions_closed": True}
    fake_trading_client.close_all_positions.assert_called_once_with(cancel_orders=True)


def test_metrics(client, store):
    store.record_trade("AAPL", "sell", 1, 100, "paper", pnl=5.0)
    r = client.get("/metrics").json()
    assert r["num_trades"] == 1 and r["win_rate"] == 1.0
