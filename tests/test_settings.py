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
