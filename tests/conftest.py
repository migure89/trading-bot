"""Fixtures comunes: datos sintéticos y mocks de Alpaca (sin credenciales)."""
from __future__ import annotations

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
    return client


@pytest.fixture
def provider(ohlcv):
    p = MagicMock()
    p.get_bars.return_value = ohlcv
    return p
