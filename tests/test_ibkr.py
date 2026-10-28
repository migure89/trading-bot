"""Tests de Interactive Brokers con un IB falso: no requieren TWS, Gateway ni red."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from config.settings import LiveTradingNotConfirmed
from connectors.ibkr import IBKRConfig, IBKRConnection, IBKRConnectionError, check_accounts
from data.ibkr_data import IBKRDataProvider, bars_to_df, chunk_plan
from execution.ibkr_broker import IBKRBroker
from main import TradingBot
from tests.conftest import FakeIB, ib_portfolio_item


def make_bars(n=48, start=datetime(2026, 10, 1, tzinfo=UTC)):
    return [SimpleNamespace(date=start + timedelta(hours=i), open=100 + i, high=101 + i, low=99 + i,
                            close=100.5 + i, volume=1000) for i in range(n)]


def make_conn(fake, **cfg):
    params = {"port_paper": 4002, "port_live": 4001, "pacing_sec": 0, **cfg}
    return IBKRConnection(IBKRConfig(**params), ib_factory=lambda: fake)


# ---- puertos, modos y cuentas ----------------------------------------------
def test_port_by_mode(monkeypatch):
    monkeypatch.delenv("CONFIRM_LIVE_TRADING", raising=False)
    cfg = IBKRConfig(port_paper=7497, port_live=7496)
    assert cfg.port_for("paper") == 7497
    with pytest.raises(LiveTradingNotConfirmed):
        cfg.port_for("live")
    monkeypatch.setenv("CONFIRM_LIVE_TRADING", "true")
    assert cfg.port_for("live") == 7496


def test_crossed_ports_rejected(monkeypatch):
    monkeypatch.setenv("CONFIRM_LIVE_TRADING", "true")
    with pytest.raises(IBKRConnectionError):
        IBKRConfig(port_paper=4001).port_for("paper")
    with pytest.raises(IBKRConnectionError):
        IBKRConfig(port_live=7497).port_for("live")


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("IBKR_HOST", "ib-gateway")
    monkeypatch.setenv("IBKR_PORT_PAPER", "4004")
    monkeypatch.setenv("IBKR_CLIENT_ID", "3")
    monkeypatch.setenv("IBKR_READONLY", "true")
    cfg = IBKRConfig.from_env()
    assert (cfg.host, cfg.port_paper, cfg.client_id, cfg.readonly) == ("ib-gateway", 4004, 3, True)


def test_check_accounts():
    assert check_accounts("paper", ["DU111"]) == "DU111"
    assert check_accounts("live", ["U999", "U888"], "U888") == "U888"
    with pytest.raises(IBKRConnectionError):
        check_accounts("paper", ["U999"])        # cuenta real en modo paper
    with pytest.raises(IBKRConnectionError):
        check_accounts("live", ["DU111"])        # cuenta paper en modo live
    with pytest.raises(IBKRConnectionError):
        check_accounts("paper", ["DU111"], "DU222")


# ---- conexión ----------------------------------------------------------------
def test_connect_reuses_and_switches_mode(monkeypatch):
    fake = FakeIB(accounts=["DU1"])
    conn = make_conn(fake, client_id=9)
    conn.ensure_connected("paper")
    conn.ensure_connected("paper")
    assert fake.connect_calls == [("127.0.0.1", 4002, 9)]
    assert conn.status()["connected"] and conn.status()["account"] == "DU1"
    # cambiar a live con una sesión paper: se rechaza y se desconecta
    monkeypatch.setenv("CONFIRM_LIVE_TRADING", "true")
    with pytest.raises(IBKRConnectionError):
        conn.ensure_connected("live")
    assert fake.connect_calls[-1][1] == 4001 and not fake.connected
    assert "paper" in conn.status()["last_error"]


def test_reconnects_after_drop():
    fake = FakeIB()
    conn = make_conn(fake)
    conn.ensure_connected("paper")
    fake.connected = False                      # TWS se reinicia
    conn.ensure_connected("paper")
    assert len(fake.connect_calls) == 2


def test_connect_failure_is_reported():
    conn = make_conn(FakeIB(fail_connect=True))
    with pytest.raises(IBKRConnectionError, match="No se pudo conectar"):
        conn.ensure_connected("paper")
    assert conn.status()["connected"] is False


def test_contracts():
    conn = make_conn(FakeIB())
    stock, crypto = conn.make_contract("AAPL"), conn.make_contract("BTC/USD")
    assert (stock.secType, stock.exchange, stock.currency) == ("STK", "SMART", "USD")
    assert (crypto.secType, crypto.symbol, crypto.exchange) == ("CRYPTO", "BTC", "PAXOS")


# ---- datos históricos --------------------------------------------------------
def test_chunk_plan():
    assert chunk_plan(90, 50) == [50, 40]
    assert chunk_plan(10, 50) == [10]
    assert chunk_plan(0, 5) == [1]


def test_bars_to_df():
    df = bars_to_df(make_bars(5))
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "UTC" and len(df) == 5
    assert bars_to_df([]).empty


def test_historical_chunked_and_deduplicated():
    end = datetime(2026, 10, 3, tzinfo=UTC)
    fake = FakeIB(bars=make_bars(48, start=end - timedelta(hours=48)))
    sleeps = []
    provider = IBKRDataProvider(make_conn(fake), sleep=sleeps.append)
    df = provider.get_bars("AAPL", "1Hour", days=90, end=end)
    assert [c["durationStr"] for c in fake.hist_calls] == ["50 D", "40 D"]
    assert fake.hist_calls[0]["whatToShow"] == "TRADES" and fake.hist_calls[0]["barSizeSetting"] == "1 hour"
    assert fake.hist_calls[1]["endDateTime"] == end - timedelta(days=50)
    assert len(df) == 48 and df.index.is_monotonic_increasing   # duplicados eliminados
    assert sleeps == [0]


def test_crypto_history_uses_aggtrades():
    fake = FakeIB(bars=make_bars(10))
    IBKRDataProvider(make_conn(fake)).get_bars("BTC/USD", "1Day", days=30)
    assert fake.hist_calls[0]["whatToShow"] == "AGGTRADES"
    assert fake.hist_calls[0]["barSizeSetting"] == "1 day"


def test_history_feeds_backtest():
    from backtesting.engine import run_backtest
    from config.settings import DEFAULT_SETTINGS

    fake = FakeIB(bars=make_bars(300, start=datetime(2026, 1, 1, tzinfo=UTC)))
    df = IBKRDataProvider(make_conn(fake)).get_bars("AAPL", "1Hour", days=20,
                                                    end=datetime(2026, 1, 14, tzinfo=UTC))
    res = run_backtest(df, DEFAULT_SETTINGS["strategy_params"])
    assert "sharpe" in res.metrics


# ---- broker ------------------------------------------------------------------
def test_broker_requires_live_confirmation(ibkr_conn):
    broker = IBKRBroker(ibkr_conn)
    with pytest.raises(LiveTradingNotConfirmed):
        broker.ensure_mode("live")
    with pytest.raises(RuntimeError):
        broker.account()


def test_account_and_positions(ibkr_conn, fake_ib):
    fake_ib.portfolio_items = [ib_portfolio_item("AAPL", 10), ib_portfolio_item("BTC", 0.5, "CRYPTO"),
                               ib_portfolio_item("MSFT", 0)]
    broker = IBKRBroker(ibkr_conn)
    broker.ensure_mode("paper")
    acc = broker.account()
    assert acc == {"equity": 100000.0, "cash": 40000.0, "buying_power": 200000.0,
                   "daily_pnl": 500.0, "daily_pnl_pct": pytest.approx(500 / 99500 * 100)}
    pos = broker.positions()
    assert [p["symbol"] for p in pos] == ["AAPL", "BTC/USD"]       # las posiciones a 0 se ignoran
    assert broker.position_qty("BTC/USD") == 0.5 and broker.position_qty("AAPL") == 10
    assert fake_ib.pnl_requests == ["DU1234567"]


def test_market_orders(ibkr_conn, fake_ib):
    broker = IBKRBroker(ibkr_conn)
    broker.ensure_mode("paper")
    order = broker.market_order("AAPL", 5, "buy")
    contract, o = fake_ib.orders[-1]
    assert order.id == "101" and (o.action, o.totalQuantity, o.tif) == ("BUY", 5, "DAY")
    assert o.account == "DU1234567" and contract.symbol == "AAPL"
    broker.market_order("BTC/USD", 0.25, "sell")
    _, o = fake_ib.orders[-1]
    assert (o.action, o.tif) == ("SELL", "IOC")


def test_close_position_and_kill_switch(ibkr_conn, fake_ib):
    fake_ib.portfolio_items = [ib_portfolio_item("AAPL", 10), ib_portfolio_item("TSLA", -3)]
    broker = IBKRBroker(ibkr_conn)
    broker.ensure_mode("paper")
    broker.close_position("TSLA")
    assert (fake_ib.orders[-1][1].action, fake_ib.orders[-1][1].totalQuantity) == ("BUY", 3)
    assert broker.close_position("NFLX") is None
    results = broker.close_all()
    assert fake_ib.global_cancels == 1 and len(results) == 2
    assert [o.action for _, o in fake_ib.orders[-2:]] == ["SELL", "BUY"]


# ---- bot con selección en caliente ------------------------------------------
def test_bot_hot_switches_broker(store, fake_trading_client, ibkr_conn, fake_ib, monkeypatch):
    import main
    from core.registry import Registry
    from execution.broker import AlpacaBroker
    from execution.factory import BrokerRegistry

    monkeypatch.setattr(main, "latest_action", lambda df, p: "buy")
    # Barras recientes: el proveedor filtra por lookback_days respecto a "ahora"
    fake_ib.bars = make_bars(200, start=datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
                             - timedelta(hours=200))
    alpaca_provider = SimpleNamespace(name="alpaca", get_bars=lambda *a, **k: pd.DataFrame())
    brokers = BrokerRegistry({"alpaca": lambda: AlpacaBroker(lambda paper: fake_trading_client),
                              "ibkr": lambda: IBKRBroker(ibkr_conn)})
    providers = Registry({"alpaca": lambda: alpaca_provider,
                          "ibkr": lambda: IBKRDataProvider(ibkr_conn)})
    bot = TradingBot(store, providers, brokers)
    store.update({"bot_enabled": True, "symbols": ["AAPL"]})
    assert bot.iteration()["broker"] == "alpaca"

    store.update({"broker": "ibkr"})                 # hot-reload: siguiente iteración con IBKR
    out = bot.iteration()
    assert out == {"status": "ok", "broker": "ibkr", "actions": {"AAPL": "buy"}}
    assert fake_ib.orders and fake_ib.orders[-1][1].action == "BUY"
    assert store.trades()[0]["broker"] == "ibkr"
    fake_trading_client.submit_order.assert_not_called()


def test_bot_reports_broker_unavailable(store):
    fake = FakeIB(fail_connect=True)
    bot = TradingBot(store, IBKRDataProvider(make_conn(fake)), IBKRBroker(make_conn(fake)))
    store.update({"broker": "ibkr"})
    out = bot.iteration()
    assert out["status"] == "broker_unavailable" and out["broker"] == "ibkr"
