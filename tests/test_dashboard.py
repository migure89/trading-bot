"""Tests del dashboard Streamlit con AppTest y una API falsa (sin red)."""
from __future__ import annotations

from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")

CONFIG = {"mode": "paper", "broker": "alpaca", "data_provider": "auto", "bot_enabled": True,
          "symbols": ["AAPL"], "risk_pct": 1.0, "timeframe": "1Hour", "loop_interval_sec": 60,
          "lookback_days": 30, "strategy_params": {"ema_fast": 12, "ema_slow": 26, "atr_stop_mult": 2.0}}


class FakeResponse:
    def __init__(self, status: int, payload=None, text: str | None = None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else ""
        self.reason = "Error"

    def json(self):
        if self._payload is None:
            raise requests.JSONDecodeError("Expecting value", self.text, 0)
        return self._payload


class FakeAPI:
    """Responde como la API; ``fail`` permite simular errores por ruta."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.fail: dict[tuple[str, str], FakeResponse] = {}

    def __call__(self, method, url, timeout=None, **kw):
        path = url.split("8000", 1)[1]
        self.calls.append((method, path, kw))
        if (method, path) in self.fail:
            return self.fail[(method, path)]
        if path == "/status":
            return FakeResponse(200, {"mode": "paper", "broker": "alpaca", "bot_enabled": True,
                                      "account": None, "positions": [], "error": "sin claves",
                                      "connection": {"connected": False}})
        if path == "/config":
            return FakeResponse(200, CONFIG)
        if path == "/metrics":
            return FakeResponse(200, {"trades": [], "optimization_history": []})
        if path == "/brokers":
            return FakeResponse(200, {"available": ["alpaca", "ibkr"], "connections": {}})
        return FakeResponse(200, {"ok": True})


@pytest.fixture
def fake_api(monkeypatch):
    monkeypatch.setenv("API_URL", "http://api:8000")
    fake = FakeAPI()
    monkeypatch.setattr(requests, "request", fake)
    return fake


def run_app() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def posts(fake, path):
    return [c for c in fake.calls if c[0] == "POST" and c[1] == path]


def test_kill_switch_requires_confirmation(fake_api):
    at = run_app()
    at.button(key="kill").click().run()
    assert not posts(fake_api, "/toggle-bot")  # el primer clic solo pide confirmación
    assert any("cierra **todas**" in e.value for e in at.error)
    at.button(key="kill_no").click().run()
    assert not posts(fake_api, "/toggle-bot")
    at.button(key="kill").click().run()
    at.button(key="kill_yes").click().run()
    calls = posts(fake_api, "/toggle-bot")
    assert len(calls) == 1 and calls[0][2]["json"] == {"enabled": False, "close_positions": True}
    assert any("Kill switch ejecutado" in s.value for s in at.success)


def test_kill_switch_error_is_visible(fake_api):
    fake_api.fail[("POST", "/toggle-bot")] = FakeResponse(
        503, {"detail": "El bot quedó pausado, pero NO se pudieron cerrar las posiciones: sin claves"})
    at = run_app()
    at.button(key="kill").click().run()
    at.button(key="kill_yes").click().run()
    assert not at.exception
    errors = " ".join(e.value for e in at.error)
    assert "503" in errors and "NO se pudieron cerrar" in errors


def test_non_json_error_does_not_crash(fake_api):
    fake_api.fail[("GET", "/metrics")] = FakeResponse(502, None, text="Bad Gateway")
    at = run_app()
    assert any("502: Bad Gateway" in e.value for e in at.error)


def test_dashboard_sends_api_token(fake_api, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "s3creto")
    run_app()
    assert fake_api.calls and all(
        c[2].get("headers", {}).get("Authorization") == "Bearer s3creto" for c in fake_api.calls)


def test_dashboard_password_gate(fake_api, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "clave")
    at = run_app()
    assert not fake_api.calls  # no se consulta la API sin contraseña
    at.text_input(key="dashboard_password").input("mala").run()
    assert any("incorrecta" in e.value for e in at.error) and not fake_api.calls
    at.text_input(key="dashboard_password").input("clave").run()
    assert any(c[1] == "/status" for c in fake_api.calls)
