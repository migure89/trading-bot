import json

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from config.settings import TIMEFRAMES, SettingsStore
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
    assert info["active"] == "ibkr" and info["connections"]["ibkr"]["account"] == "DU****567"


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


# ---- Regresiones issue #2: Alpaca sin credenciales / errores del broker ----
@pytest.fixture
def client_no_keys(store, monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    registry = BrokerRegistry({"alpaca": AlpacaBroker})
    api_main.app.dependency_overrides[api_main.get_store] = lambda: store
    api_main.app.dependency_overrides[api_main.get_brokers] = lambda: registry
    yield TestClient(api_main.app, raise_server_exceptions=False)
    api_main.app.dependency_overrides.clear()


@pytest.mark.parametrize("path,body", [
    ("/cancel-orders", None),
    ("/close-position", {"symbol": "AAPL"}),
    ("/toggle-bot", {"enabled": False, "close_positions": True}),
])
def test_alpaca_without_keys_returns_503(client_no_keys, path, body):
    r = client_no_keys.post(path, json=body)
    assert r.status_code == 503
    assert "APCA_API_KEY_ID" in r.json()["detail"]


def test_kill_switch_error_says_bot_is_paused(client_no_keys, store):
    store.update({"bot_enabled": True})
    r = client_no_keys.post("/toggle-bot", json={"enabled": False, "close_positions": True})
    assert r.status_code == 503 and "pausado" in r.json()["detail"]
    assert store.get("bot_enabled") is False


def test_status_reports_last_error_without_keys(client_no_keys):
    r = client_no_keys.get("/status").json()
    assert "APCA_API_KEY_ID" in r["error"]
    assert r["connection"]["credentials"] is False
    assert "APCA_API_KEY_ID" in r["connection"]["last_error"]


def test_broker_rejection_returns_502(client, fake_trading_client):
    fake_trading_client.cancel_orders.side_effect = RuntimeError("forbidden")
    r = client.post("/cancel-orders")
    assert r.status_code == 502 and "forbidden" in r.json()["detail"]


# ---- Regresiones issue #12: autenticación por API_TOKEN y datos de cuenta enmascarados ----
@pytest.mark.parametrize("method,path", [
    ("get", "/status"), ("get", "/config"), ("post", "/config"), ("post", "/toggle-bot"),
    ("post", "/cancel-orders"), ("post", "/close-position"), ("get", "/metrics"), ("get", "/brokers"),
])
def test_endpoints_require_token(client, monkeypatch, method, path):
    monkeypatch.setenv("API_TOKEN", "s3creto")
    r = getattr(client, method)(path)
    assert r.status_code == 401
    r = getattr(client, method)(path, headers={"Authorization": "Bearer otro"})
    assert r.status_code == 401


def test_token_accepted_in_bearer_and_x_api_key(client, monkeypatch, fake_trading_client):
    monkeypatch.setenv("API_TOKEN", "s3creto")
    assert client.get("/status", headers={"Authorization": "Bearer s3creto"}).status_code == 200
    assert client.get("/status", headers={"X-API-Key": "s3creto"}).status_code == 200
    r = client.post("/toggle-bot", json={"enabled": False, "close_positions": True},
                    headers={"X-API-Key": "s3creto"})
    assert r.status_code == 200
    fake_trading_client.close_all_positions.assert_called_once()


def test_health_is_public(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "s3creto")
    assert client.get("/health").json() == {"ok": True}


def test_without_token_auth_is_disabled(client, monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    assert client.get("/config").status_code == 200


def test_status_masks_ibkr_account(client):
    client.post("/config", json={"broker": "ibkr"})
    conn = client.get("/status").json()["connection"]
    assert conn["account"] == "DU****567" and conn["accounts"] == ["DU****567"]


# ---- issue #5: validación estricta de /config y del store ---------------------
@pytest.mark.parametrize("body", [
    {"timeframe": "2Weeks"},
    {"symbols": []},
    {"symbols": ["", "AAPL"]},
    {"symbols": ["AAPL; DROP"]},
    {"symbols": "AAPL"},
    {"strategy_params": {"ema_fast": -5}},
    {"strategy_params": {"ema_slow": "abc"}},
    {"strategy_params": {"nuevo": 1}},
    {"strategy_params": {"ema_fast": 30, "ema_slow": 20}},
    {"strategy_params": {"rsi_oversold": 80, "rsi_overbought": 70}},
    {"strategy_params": {"rsi_overbought": 150}},
    {"lookback_days": 100000},
    {"lookback_days": 0},
    {"loop_interval_sec": 1},
    {"risk_pct": 11},
    {"foo": 1},
    {"bot_enabled": True},          # se cambia con /toggle-bot
])
def test_invalid_config_is_rejected(client, body):
    before = client.get("/config").json()
    r = client.post("/config", json=body)
    assert r.status_code == 422, r.text
    assert client.get("/config").json() == before


def test_symbols_are_normalized(client):
    r = client.post("/config", json={"symbols": [" aapl", "MSFT", "AAPL", "btc/usd"]})
    assert r.status_code == 200
    assert r.json()["symbols"] == ["AAPL", "MSFT", "BTC/USD"]


def test_partial_strategy_params_merge(client):
    r = client.post("/config", json={"strategy_params": {"ema_fast": 8}}).json()
    assert r["strategy_params"]["ema_fast"] == 8 and r["strategy_params"]["ema_slow"] == 26


def test_unknown_strategy_keys_in_db_are_dropped(tmp_path):
    store = SettingsStore(tmp_path / "s.db")
    with store._conn() as conn:  # BD "contaminada" por la versión anterior
        conn.execute("UPDATE settings SET value=? WHERE key='strategy_params'",
                     (json.dumps({**store.get("strategy_params"), "nuevo": 1}),))
    params = store.update({"strategy_params": {"ema_fast": 10}})["strategy_params"]
    assert "nuevo" not in params and params["ema_fast"] == 10


@pytest.mark.parametrize("changes", [
    {"timeframe": "2Weeks"}, {"symbols": []}, {"lookback_days": 100000},
    {"strategy_params": {"ema_slow": "abc"}}, {"strategy_params": {"ema_fast": 50}},
    {"bot_enabled": "yes"},
])
def test_store_validates_too(tmp_path, changes):
    """El optimizador y el bot escriben directamente en el store: también valida."""
    with pytest.raises(ValueError):
        SettingsStore(tmp_path / "s.db").update(changes)


def test_api_timeframes_match_settings():
    field = api_main.ConfigUpdate.model_fields["timeframe"]
    assert set(field.annotation.__args__[0].__args__) == set(TIMEFRAMES)


def test_unknown_keys_rejected_in_toggle(client):
    assert client.post("/toggle-bot", json={"enable": True}).status_code == 422


def test_openapi_still_builds(client):
    assert TestClient(api_main.app).get("/openapi.json").status_code == 200
