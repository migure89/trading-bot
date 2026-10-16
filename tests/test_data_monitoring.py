import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from data.alpaca_data import AlpacaDataProvider, is_crypto, normalize_bars
from monitoring.logger import JsonFormatter


def _multi(df, symbol):
    df = df.copy()
    df.index = pd.MultiIndex.from_product([[symbol], df.index], names=["symbol", "timestamp"])
    return df


def test_routes_stock_and_crypto(ohlcv):
    stock, crypto = MagicMock(), MagicMock()
    stock.get_stock_bars.return_value = SimpleNamespace(df=_multi(ohlcv, "AAPL"))
    crypto.get_crypto_bars.return_value = SimpleNamespace(df=_multi(ohlcv, "BTC/USD"))
    p = AlpacaDataProvider("k", "s", stock_client=stock, crypto_client=crypto)
    assert len(p.get_bars("AAPL")) == len(ohlcv)
    assert len(p.get_bars("BTC/USD")) == len(ohlcv)
    assert is_crypto("BTC/USD") and not is_crypto("AAPL")


def test_normalize_empty():
    assert normalize_bars(pd.DataFrame(), "X").empty


def test_json_log():
    rec = logging.LogRecord("t", logging.INFO, "", 0, "hola", (), None)
    rec.symbol = "AAPL"
    out = json.loads(JsonFormatter().format(rec))
    assert out["msg"] == "hola" and out["symbol"] == "AAPL"
