"""Issue #19: barras de Alpaca solo en sesión regular y Sharpe anualizado con las barras reales."""
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from backtesting.engine import run_backtest
from backtesting.metrics import PERIODS_PER_YEAR, infer_periods_per_year
from config.settings import SettingsStore
from data.alpaca_data import AlpacaDataProvider
from data.base import NY, default_sessions, regular_session_bars

END = datetime(2026, 10, 23, 23, 59, tzinfo=UTC)          # viernes


def _bars(freq: str, start="2026-10-19 08:00", end="2026-10-24 00:00") -> pd.DataFrame:
    """Barras de 04:00 a 20:00 ET (pre-market + regular + after-hours), indexadas en UTC."""
    idx = pd.date_range(start, end, freq=freq, tz="UTC", inclusive="left")
    et = idx.tz_convert(NY)
    keep = (et.hour >= 4) & (et.hour < 20) & (et.day_of_week < 5)
    idx = idx[keep]
    n = len(idx)
    close = 100 + np.arange(n) * 0.01
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close,
                         "volume": np.ones(n)}, index=idx)


def _multi(df, symbol="AAPL"):
    df = df.copy()
    df.index = pd.MultiIndex.from_product([[symbol], df.index], names=["symbol", "timestamp"])
    return df


def _calendar(early_close: date | None = None):
    """Calendario fake con el formato de alpaca-py (open/close naive en hora de Nueva York)."""
    days = []
    for d, (o, c) in default_sessions(datetime(2026, 10, 19, tzinfo=UTC), END).items():
        close = c.replace(hour=13, minute=0) if d == early_close else c
        days.append(SimpleNamespace(date=d, open=o.tz_localize(None), close=close.tz_localize(None)))
    cal = MagicMock()
    cal.get_calendar.return_value = days
    return cal


def _provider(df, **kw):
    stock = MagicMock()
    stock.get_stock_bars.return_value = SimpleNamespace(df=_multi(df))
    return AlpacaDataProvider("k", "s", stock_client=stock, calendar_client=_calendar(), **kw), stock


def _requested_minutes(stock) -> int:
    return stock.get_stock_bars.call_args.args[0].timeframe.amount


def test_default_is_regular_hours_and_1hour_aligned_to_open(monkeypatch):
    monkeypatch.delenv("ALPACA_REGULAR_HOURS_ONLY", raising=False)
    provider, stock = _provider(_bars("30min"))
    df = provider.get_bars("AAPL", "1Hour", days=5, end=END)
    assert _requested_minutes(stock) == 30            # base de 30 min para alinear a las 09:30
    et = df.index.tz_convert(NY)
    assert ((et.hour * 60 + et.minute) >= 9 * 60 + 30).all() and (et.hour < 16).all()
    assert set(zip(et.hour, et.minute, strict=True)) == {(9, 30), (10, 30), (11, 30), (12, 30), (13, 30),
                                            (14, 30), (15, 30)}
    assert df.groupby(et.date).size().tolist() == [7] * 5
    first = df.iloc[0]                                  # 09:30-10:30 = dos barras de 30 min
    assert first["volume"] == 2 and first["high"] == 100 + 1 + 0.01 * (11 + 1)


def test_minute_bars_filtered_to_session():
    provider, _ = _provider(_bars("15min"))
    df = provider.get_bars("AAPL", "15Min", days=5, end=END)
    assert df.groupby(df.index.tz_convert(NY).date).size().tolist() == [26] * 5


def test_early_close_and_holidays_from_calendar():
    df = _bars("30min")
    out = regular_session_bars(df, "1Hour", {
        d: s for d, s in {d: (o, c.replace(hour=13, minute=0) if d == date(2026, 10, 21) else c)
                          for d, (o, c) in default_sessions(datetime(2026, 10, 19, tzinfo=UTC),
                                                            END).items()}.items()
        if d != date(2026, 10, 22)})                    # 22-oct festivo, 21-oct cierra a las 13:00
    sizes = out.groupby(out.index.tz_convert(NY).date).size()
    assert date(2026, 10, 22) not in sizes.index
    assert sizes[date(2026, 10, 21)] == 4               # 09:30, 10:30, 11:30, 12:30-13:00


def test_can_disable_regular_hours(monkeypatch):
    raw = _bars("1h")
    provider, stock = _provider(raw)
    assert len(provider.get_bars("AAPL", "1Hour", days=5, end=END, regular_hours=False)) == len(raw)
    tf = stock.get_stock_bars.call_args.args[0].timeframe
    assert tf.amount == 1 and tf.unit.value == "Hour"
    monkeypatch.setenv("ALPACA_REGULAR_HOURS_ONLY", "false")
    assert AlpacaDataProvider("k", "s").regular_hours_only is False


def test_daily_and_crypto_untouched():
    raw = _bars("1h")
    provider, _ = _provider(raw)
    crypto = MagicMock()
    crypto.get_crypto_bars.return_value = SimpleNamespace(df=_multi(raw, "BTC/USD"))
    provider._crypto = crypto
    assert len(provider.get_bars("BTC/USD", "1Hour", days=5, end=END)) == len(raw)
    assert len(provider.get_bars("AAPL", "1Day", days=5, end=END)) == len(raw)


def test_calendar_failure_falls_back_with_single_warning(caplog):
    cal = MagicMock()
    cal.get_calendar.side_effect = RuntimeError("sin red")
    stock = MagicMock()
    stock.get_stock_bars.return_value = SimpleNamespace(df=_multi(_bars("30min")))
    p = AlpacaDataProvider("k", "s", stock_client=stock, calendar_client=cal)
    for _ in range(3):
        assert len(p.get_bars("AAPL", "1Hour", days=5, end=END)) == 35
    assert sum("calendario de Alpaca no disponible" in r.getMessage() for r in caplog.records) == 1


def test_setting_validated_and_passed_to_provider(tmp_path):
    store = SettingsStore(tmp_path / "s.db")
    assert store.load()["regular_hours_only"] is True
    with pytest.raises(ValueError, match="regular_hours_only"):
        store.update({"regular_hours_only": "no"})
    assert store.update({"regular_hours_only": False})["regular_hours_only"] is False


def test_api_validates_regular_hours_only(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("SETTINGS_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.delenv("API_TOKEN", raising=False)
    from api.main import app

    client = TestClient(app)
    assert client.post("/config", json={"regular_hours_only": "yes"}).status_code == 422
    resp = client.post("/config", json={"regular_hours_only": False})
    assert resp.status_code == 200 and resp.json()["regular_hours_only"] is False


def test_bot_and_optimizer_pass_setting(store, ohlcv):
    from optimization.optimizer import run_feedback_loop
    from tests.test_backtest_optimizer import FlakyProvider

    store.update({"symbols": ["MSFT"], "regular_hours_only": False})
    provider = FlakyProvider(ohlcv, failing=())
    run_feedback_loop(store, provider, n_trials=2, apply=False)
    assert provider.regular_hours is False


# --- anualización ---------------------------------------------------------------------------------
def test_periods_per_year_follows_real_bars():
    provider, _ = _provider(_bars("30min"))
    rth = provider.get_bars("AAPL", "1Hour", days=5, end=END)
    assert infer_periods_per_year(rth.index, "1Hour") == 7 * 252 == PERIODS_PER_YEAR["1Hour"]
    extended = _bars("1h")                               # 16 barras/día
    assert infer_periods_per_year(extended.index, "1Hour") == 16 * 252
    crypto = pd.date_range("2026-10-01", periods=24 * 14, freq="h", tz="UTC")
    assert infer_periods_per_year(crypto, "1Hour") == 24 * 365
    assert infer_periods_per_year(pd.DatetimeIndex([]), "1Day") == 252


def test_backtest_sharpe_uses_inferred_periods():
    df = _bars("1h")
    rng = np.random.default_rng(1)
    df["close"] = 100 + np.cumsum(rng.normal(0, 0.5, len(df)))
    params = {"ema_fast": 3, "ema_slow": 8, "rsi_period": 5, "rsi_overbought": 90,
              "rsi_oversold": 10, "atr_period": 5, "atr_stop_mult": 2.0, "bb_period": 5, "bb_std": 2.0}
    auto = run_backtest(df, params).metrics["sharpe"]
    fixed = run_backtest(df, params, periods_per_year=16 * 252).metrics["sharpe"]
    table = run_backtest(df, params, periods_per_year=PERIODS_PER_YEAR["1Hour"]).metrics["sharpe"]
    assert auto == pytest.approx(fixed)
    if table:
        assert auto / table == pytest.approx(np.sqrt(16 / 7))
