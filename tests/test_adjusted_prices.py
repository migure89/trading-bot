"""Precios ajustados por splits/dividendos en Alpaca e IBKR (diario)."""
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest
from alpaca.data.enums import Adjustment

from connectors.ibkr import IBKRConfig, IBKRConnection
from data.alpaca_data import AlpacaDataProvider
from data.ibkr_data import IBKRDataProvider, bars_to_df
from tests.conftest import FakeIB

END = datetime(2026, 10, 23, 21, 0, tzinfo=UTC)


def _stock_client():
    idx = pd.MultiIndex.from_product(
        [["AAPL"], pd.date_range("2026-10-19 04:00", periods=5, freq="D", tz="UTC")],
        names=["symbol", "timestamp"])
    df = pd.DataFrame({c: 1.0 for c in ("open", "high", "low", "close", "volume")}, index=idx)
    stock = MagicMock()
    stock.get_stock_bars.return_value = SimpleNamespace(df=df)
    return stock


def _alpaca(**kw):
    stock = _stock_client()
    return AlpacaDataProvider("k", "s", stock_client=stock, calendar_client=MagicMock(), **kw), stock


def test_alpaca_requests_adjusted_prices_by_default(monkeypatch):
    monkeypatch.delenv("ALPACA_ADJUSTMENT", raising=False)
    provider, stock = _alpaca()
    provider.get_bars("AAPL", "1Day", days=10, end=END)
    assert stock.get_stock_bars.call_args.args[0].adjustment == Adjustment.ALL


@pytest.mark.parametrize("value,expected", [("raw", Adjustment.RAW), ("split", Adjustment.SPLIT),
                                            ("bogus", Adjustment.ALL)])
def test_alpaca_adjustment_from_env(monkeypatch, value, expected):
    monkeypatch.setenv("ALPACA_ADJUSTMENT", value)
    provider, stock = _alpaca()
    provider.get_bars("AAPL", "1Day", days=10, end=END)
    assert stock.get_stock_bars.call_args.args[0].adjustment == expected


def test_alpaca_adjustment_argument_validated():
    assert _alpaca(adjustment="dividend")[0].adjustment == "dividend"
    with pytest.raises(ValueError):
        _alpaca(adjustment="nope")


def test_alpaca_crypto_has_no_adjustment():
    crypto = MagicMock()
    crypto.get_crypto_bars.return_value = SimpleNamespace(df=pd.DataFrame())
    provider = AlpacaDataProvider("k", "s", crypto_client=crypto)
    provider.get_bars("BTC/USD", "1Day", days=5, end=END)
    assert not hasattr(crypto.get_crypto_bars.call_args.args[0], "adjustment")


def _daily_bars(n=600, last=date(2026, 10, 23)):
    days = pd.bdate_range(end=last, periods=n)
    return [SimpleNamespace(date=d.date(), open=1, high=1, low=1, close=float(i), volume=10)
            for i, d in enumerate(days)]


def _ibkr(fake, **kw):
    conn = IBKRConnection(IBKRConfig(port_paper=4002, port_live=4001, pacing_sec=0), ib_factory=lambda: fake)
    return IBKRDataProvider(conn, **kw)


def test_ibkr_daily_uses_adjusted_last_single_request(monkeypatch):
    monkeypatch.delenv("IBKR_ADJUSTED_DAILY", raising=False)
    fake = FakeIB(bars=_daily_bars())
    end = datetime.now(UTC)
    df = _ibkr(fake).get_bars("AAPL", "1Day", days=420, end=end)
    assert len(fake.hist_calls) == 1
    call = fake.hist_calls[0]
    assert call["whatToShow"] == "ADJUSTED_LAST" and call["endDateTime"] == ""
    assert call["durationStr"] == "2 Y" and call["barSizeSetting"] == "1 day"
    assert df.index.min() >= pd.Timestamp(end - timedelta(days=420))


def test_ibkr_adjusted_daily_trimmed_to_end_and_can_be_disabled(monkeypatch):
    fake = FakeIB(bars=_daily_bars())
    end = datetime(2026, 9, 30, 23, tzinfo=UTC)
    df = _ibkr(fake).get_bars("AAPL", "1Day", days=100, end=end)
    assert df.index.max() <= pd.Timestamp(end)
    monkeypatch.setenv("IBKR_ADJUSTED_DAILY", "false")
    fake2 = FakeIB(bars=_daily_bars())
    _ibkr(fake2).get_bars("AAPL", "1Day", days=100, end=end)
    assert fake2.hist_calls[0]["whatToShow"] == "TRADES"


def test_ibkr_intraday_unchanged():
    fake = FakeIB(bars=[])
    _ibkr(fake, adjusted_daily=True).get_bars("AAPL", "1Hour", days=10, end=END)
    assert fake.hist_calls[0]["whatToShow"] == "TRADES" and fake.hist_calls[0]["endDateTime"] == END


def test_ibkr_daily_dates_at_new_york_midnight():
    df = bars_to_df(_daily_bars(n=2, last=date(2026, 10, 23)))
    assert df.index[-1] == pd.Timestamp("2026-10-23 04:00", tz="UTC")   # 00:00 ET (EDT)
    assert df.index[-1].tz_convert("America/New_York").date() == date(2026, 10, 23)
