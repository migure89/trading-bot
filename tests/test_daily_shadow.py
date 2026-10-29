"""Modo diario (calendario, migración de config) y estrategia en sombra (sin órdenes)."""
import ast
import inspect
import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

import main
from config.settings import SettingsStore
from core.schedule import DailyGate, completed_daily_bars, effective_lookback, parse_hhmm
from execution.broker import AlpacaBroker
from main import TradingBot
from optimization import optimizer
from shadow.runner import ShadowRunner

ROOT = Path(__file__).resolve().parents[1]
THU_10ET = datetime(2026, 10, 29, 14, 0, tzinfo=UTC)     # jueves 10:00 ET (EDT)
THU_9ET = datetime(2026, 10, 29, 13, 0, tzinfo=UTC)
THU_1555ET = datetime(2026, 10, 29, 19, 57, tzinfo=UTC)
SAT_10ET = datetime(2026, 10, 31, 14, 0, tzinfo=UTC)


def daily_bars(seed: int, n=320, last=date(2026, 10, 29), drift=0.0008) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=last, periods=n, tz="America/New_York").tz_convert("UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": 1e6}, index=idx)


@pytest.fixture
def daily_provider():
    p = MagicMock(spec=["get_bars"])
    p.get_bars.side_effect = lambda sym, tf, days, **kw: daily_bars(sum(map(ord, sym)))
    return p


# ---- calendario ---------------------------------------------------------------------
def test_parse_hhmm():
    assert parse_hhmm("09:35").minute == 35
    for bad in ("9:35", "24:00", "ab", ""):
        with pytest.raises(ValueError):
            parse_hhmm(bad)


def test_daily_gate_once_per_trading_day():
    gate = DailyGate()
    assert not gate.due(THU_9ET, "09:35")          # antes de la hora
    assert gate.due(THU_10ET, "09:35")
    gate.mark(THU_10ET)
    assert not gate.due(THU_1555ET, "09:35")       # ya ejecutado hoy
    assert not DailyGate().due(SAT_10ET, "09:35")  # fin de semana
    assert DailyGate().due(THU_1555ET, "09:35")


def test_trade_gate_requires_open_market_and_uses_calendar():
    assert not DailyGate(until_close=True).due(THU_1555ET, "09:35")    # 5 min antes del cierre
    assert DailyGate(until_close=True).due(THU_10ET, "09:35")
    holiday = MagicMock()
    holiday.sessions.return_value = {}                                   # festivo según el calendario
    assert not DailyGate().due(THU_10ET, "09:35", holiday)


def test_completed_daily_bars_drops_todays_partial_bar():
    df = daily_bars(1, n=5)
    assert completed_daily_bars(df, THU_10ET).index[-1].date() == date(2026, 10, 28)
    after_close = datetime(2026, 10, 29, 20, 30, tzinfo=UTC)            # 16:30 ET
    assert len(completed_daily_bars(df, after_close)) == 5


def test_effective_lookback():
    assert effective_lookback({"timeframe": "1Day", "lookback_days": 30}) == 420
    assert effective_lookback({"timeframe": "1Hour", "lookback_days": 30}) == 30


# ---- configuración y migración ------------------------------------------------------
def test_new_db_defaults_daily(daily_store):
    cfg = daily_store.load()
    assert (cfg["timeframe"], cfg["lookback_days"], cfg["daily_run_time"]) == ("1Day", 420, "09:35")
    assert cfg["shadow_strategy"] == "blend" and "SPY" in cfg["shadow_symbols"]
    assert cfg["bot_enabled"] is False


def _old_db(path, **values):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                 "updated_at TEXT NOT NULL DEFAULT (datetime('now')))")
    base = {"timeframe": "1Hour", "lookback_days": 30, "symbols": ["AAPL", "MSFT"], "risk_pct": 1.5}
    for k, v in {**base, **values}.items():
        conn.execute("INSERT INTO settings(key, value) VALUES (?, ?)", (k, json.dumps(v)))
    conn.commit()
    conn.close()


def test_migration_old_hourly_db_to_daily(tmp_path, caplog):
    path = tmp_path / "old.db"
    _old_db(path)
    cfg = SettingsStore(path).load()
    assert (cfg["timeframe"], cfg["lookback_days"]) == ("1Day", 420)
    assert cfg["symbols"] == ["AAPL", "MSFT"] and cfg["risk_pct"] == 1.5       # el resto intacto
    assert cfg["shadow_strategy"] == "blend"
    assert "configuración migrada a barras diarias" in caplog.text
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM migrations").fetchall() == [("2026-10-daily-bars",)]
    # Volver a 1Hour a mano se respeta: la migración no se repite al reabrir.
    SettingsStore(path).update({"timeframe": "1Hour", "lookback_days": 30})
    assert SettingsStore(path).load()["timeframe"] == "1Hour"


def test_migration_respects_other_timeframes(tmp_path):
    path = tmp_path / "old.db"
    _old_db(path, timeframe="15Min")
    assert SettingsStore(path).load()["timeframe"] == "15Min"
    path2 = tmp_path / "old2.db"
    _old_db(path2, timeframe="1Day", lookback_days=600)
    assert SettingsStore(path2).load()["lookback_days"] == 600


@pytest.mark.parametrize("bad", [{"daily_run_time": "9h"}, {"shadow_strategy": "ema_rsi"},
                                 {"shadow_strategy": "magic"}, {"shadow_symbols": ["bad symbol!"]}])
def test_shadow_settings_validated(daily_store, bad):
    with pytest.raises(ValueError):
        daily_store.update(bad)


# ---- sombra: nunca envía órdenes -----------------------------------------------------
def test_shadow_module_has_no_broker():
    tree = ast.parse((ROOT / "shadow" / "runner.py").read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m and (m.startswith("execution") or m.startswith("connectors")) for m in mods)
    assert list(inspect.signature(ShadowRunner).parameters) == ["store", "provider"]


def test_shadow_runner_records_snapshot(daily_store, daily_provider):
    daily_store.update({"shadow_symbols": ["SPY", "AAPL", "MSFT", "NVDA", "KO"]})
    snap = ShadowRunner(daily_store, daily_provider).run(daily_store.load(), THU_10ET)
    assert snap["shadow"] is True and snap["orders_sent"] == 0 and snap["strategy"] == "blend"
    assert snap["as_of"] == "2026-10-28"                       # última barra cerrada
    assert set(snap["weights"]) == {"SPY", "AAPL", "MSFT", "NVDA", "KO"}
    assert sum(snap["weights"].values()) <= 1 + 1e-9
    assert set(snap["components"]) == {"trend_sma", "rsi2"}
    assert daily_store.shadow_history()[0]["as_of"] == "2026-10-28"
    for _, _, kw in daily_provider.get_bars.mock_calls:
        assert kw["regular_hours"] is True
    assert {c.args[1] for c in daily_provider.get_bars.call_args_list} == {"1Day"}
    assert REGISTRY.get_sample_value("bot_shadow_gross_exposure", {"strategy": "blend"}) == pytest.approx(
        snap["gross_exposure"])


def test_enabled_bot_with_shadow_sends_no_shadow_orders(daily_store, daily_provider,
                                                        fake_trading_client, monkeypatch):
    monkeypatch.setattr(main, "latest_action", lambda df, p: "hold")    # ema_rsi no opera hoy
    daily_store.update({"bot_enabled": True, "symbols": ["AAPL"],
                        "shadow_symbols": ["SPY", "AAPL", "MSFT", "NVDA"]})
    broker = AlpacaBroker(client_factory=lambda paper: fake_trading_client)
    out = TradingBot(daily_store, daily_provider, broker, now=lambda: THU_10ET).iteration()
    assert out["shadow"] == "ok" and out["actions"] == {"AAPL": "hold"}
    assert any(w > 0 for w in daily_store.shadow_history()[0]["weights"].values())
    fake_trading_client.submit_order.assert_not_called()
    fake_trading_client.close_position.assert_not_called()


def test_paused_bot_runs_shadow_without_touching_broker(daily_store, daily_provider):
    broker = MagicMock()
    bot = TradingBot(daily_store, daily_provider, broker, healthcheck_interval=0, now=lambda: THU_10ET)
    out = bot.iteration()
    assert out["status"] == "paused" and out["shadow"] == "ok"
    assert broker.mock_calls == []
    assert "shadow" not in bot.iteration()                    # una vez al día
    assert len(daily_store.shadow_history()) == 1


def test_shadow_error_does_not_break_bot(daily_store):
    provider = MagicMock(spec=["get_bars"])
    provider.get_bars.side_effect = RuntimeError("sin red")
    bot = TradingBot(daily_store, provider, MagicMock(), healthcheck_interval=0, now=lambda: THU_10ET)
    out = bot.iteration()
    assert out["status"] == "paused" and out["shadow"] == "error"
    assert "shadow" not in bot.iteration()                    # reintento en 15 min, no en cada vuelta


def test_shadow_off(daily_store, daily_provider):
    daily_store.update({"shadow_strategy": "off"})
    bot = TradingBot(daily_store, daily_provider, MagicMock(), healthcheck_interval=0, now=lambda: THU_10ET)
    assert "shadow" not in bot.iteration()
    daily_provider.get_bars.assert_not_called()


# ---- bot diario ---------------------------------------------------------------------
def test_daily_bot_trades_once_at_run_time(daily_store, daily_provider, fake_trading_client, monkeypatch):
    monkeypatch.setattr(main, "latest_action", lambda df, p: "buy")
    daily_store.update({"bot_enabled": True, "symbols": ["AAPL"], "shadow_strategy": "off"})
    clock = {"now": THU_9ET}
    broker = AlpacaBroker(client_factory=lambda paper: fake_trading_client)
    bot = TradingBot(daily_store, daily_provider, broker, healthcheck_interval=0, now=lambda: clock["now"])
    assert bot.iteration()["status"] == "waiting_daily_run"
    fake_trading_client.submit_order.assert_not_called()
    clock["now"] = THU_10ET
    assert bot.iteration()["actions"]["AAPL"] == "buy"
    assert bot.iteration()["status"] == "waiting_daily_run"
    fake_trading_client.submit_order.assert_called_once()
    _, tf, days = daily_provider.get_bars.call_args.args[:3]
    assert (tf, days) == ("1Day", 420)


# ---- optimizador -------------------------------------------------------------------
def test_optimizer_daily_history_and_never_touches_shadow(daily_store, monkeypatch):
    assert optimizer.history_days(daily_store.load()) == 1825
    assert optimizer.history_days({"timeframe": "1Hour", "lookback_days": 30}) == 90
    before = {k: v for k, v in daily_store.load().items() if k.startswith(("shadow_", "daily_"))}
    optimizer.apply_params(daily_store, {"ema_fast": 10, "ema_slow": 30}, "AAPL", {"sharpe": 1})
    after = daily_store.load()
    assert {k: after[k] for k in before} == before
    assert after["strategy_params"]["ema_fast"] == 10


# ---- API y dashboard ------------------------------------------------------------------
def test_shadow_endpoint_and_config(daily_store, daily_provider):
    from api import main as api_main

    api_main.app.dependency_overrides[api_main.get_store] = lambda: daily_store
    try:
        client = TestClient(api_main.app)
        r = client.get("/shadow").json()
        assert r["strategy"] == "blend" and r["orders"] is False and r["latest"] is None
        ShadowRunner(daily_store, daily_provider).run(daily_store.load(), THU_10ET)
        r = client.get("/shadow").json()
        assert r["latest"]["orders_sent"] == 0 and len(r["history"]) == 1
        assert client.post("/config", json={"shadow_strategy": "rsi2"}).json()["shadow_strategy"] == "rsi2"
        assert client.post("/config", json={"shadow_strategy": "ema_rsi"}).status_code == 422
        assert client.post("/config", json={"daily_run_time": "25:00"}).status_code == 422
        assert client.post("/config", json={"daily_run_time": "10:00"}).json()["daily_run_time"] == "10:00"
    finally:
        api_main.app.dependency_overrides.clear()


def test_dashboard_shadow_tab(monkeypatch):
    import requests
    from streamlit.testing.v1 import AppTest

    from tests.test_dashboard import APP, FakeAPI, FakeResponse

    snap = {"strategy": "blend", "as_of": "2026-10-28", "regime_bull": True, "gross_exposure": 0.6,
            "weights": {"SPY": 0.35, "AAPL": 0.25}, "orders_sent": 0, "errors": {"XOM": "sin datos"},
            "changes": [{"symbol": "AAPL", "from": 0, "to": 0.25, "action": "entrar"}]}

    class ShadowAPI(FakeAPI):
        def __call__(self, method, url, timeout=None, **kw):
            if url.endswith("/shadow"):
                return FakeResponse(200, {"strategy": "blend", "daily_run_time": "09:35", "orders": False,
                                          "latest": snap, "history": [snap, snap]})
            return super().__call__(method, url, timeout, **kw)

    monkeypatch.setenv("API_URL", "http://api:8000")
    monkeypatch.setattr(requests, "request", ShadowAPI())
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    assert any("2026-10-28" in str(m.value) for m in at.metric)
    assert any("XOM" in w.value for w in at.warning)
