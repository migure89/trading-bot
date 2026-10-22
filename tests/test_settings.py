import pytest

from config.settings import LiveTradingNotConfirmed


def test_defaults_seeded(store):
    cfg = store.load()
    assert cfg["mode"] == "paper"
    assert cfg["bot_enabled"] is False
    assert "ema_fast" in cfg["strategy_params"]


def test_hot_reload_persists(store, tmp_path):
    store.update({"risk_pct": 2.5, "symbols": ["BTC/USD"]})
    from config.settings import SettingsStore

    other = SettingsStore(store.db_path)  # otro proceso vería el cambio
    assert other.load()["risk_pct"] == 2.5
    assert other.load()["symbols"] == ["BTC/USD"]


def test_live_requires_confirmation(store, monkeypatch):
    with pytest.raises(LiveTradingNotConfirmed):
        store.update({"mode": "live"})
    monkeypatch.setenv("CONFIRM_LIVE_TRADING", "true")
    assert store.update({"mode": "live"})["mode"] == "live"


def test_invalid_values(store):
    with pytest.raises(ValueError):
        store.update({"risk_pct": 50})
    with pytest.raises(ValueError):
        store.update({"foo": 1})


def test_strategy_params_merge(store):
    store.update({"strategy_params": {"ema_fast": 8}})
    p = store.get("strategy_params")
    assert p["ema_fast"] == 8 and p["ema_slow"] == 26


def test_broker_settings(store, monkeypatch):
    from config.settings import LiveTradingNotConfirmed, resolve_data_provider

    cfg = store.load()
    assert cfg["broker"] == "alpaca" and resolve_data_provider(cfg) == "alpaca"
    cfg = store.update({"broker": "IBKR"})
    assert cfg["broker"] == "ibkr" and resolve_data_provider(cfg) == "ibkr"
    assert resolve_data_provider(store.update({"data_provider": "alpaca"})) == "alpaca"
    with pytest.raises(ValueError):
        store.update({"broker": "binance"})
    with pytest.raises(ValueError):
        store.update({"data_provider": "yahoo"})
    monkeypatch.setenv("CONFIRM_LIVE_TRADING", "true")
    store.update({"mode": "live"})
    monkeypatch.delenv("CONFIRM_LIVE_TRADING")
    with pytest.raises(LiveTradingNotConfirmed):
        store.update({"broker": "alpaca"})


def test_migrates_old_trades_table(tmp_path):
    import sqlite3

    from config.settings import SettingsStore

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, "
                     "side TEXT NOT NULL, qty REAL NOT NULL, price REAL, mode TEXT NOT NULL, order_id TEXT, "
                     "pnl REAL, created_at TEXT NOT NULL DEFAULT (datetime('now')))")
    store = SettingsStore(db)
    store.record_trade("AAPL", "buy", 1, 100, "paper", broker="ibkr")
    assert store.trades()[0]["broker"] == "ibkr"
