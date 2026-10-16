from types import SimpleNamespace

import pytest

from config.settings import LiveTradingNotConfirmed
from execution.broker import AlpacaBroker
from main import TradingBot


def make_broker(client):
    calls = []

    def factory(paper):
        calls.append(paper)
        return client
    return AlpacaBroker(client_factory=factory), calls


def test_mode_switch(fake_trading_client, monkeypatch):
    broker, calls = make_broker(fake_trading_client)
    broker.ensure_mode("paper")
    broker.ensure_mode("paper")
    assert calls == [True]
    monkeypatch.delenv("CONFIRM_LIVE_TRADING", raising=False)
    with pytest.raises(LiveTradingNotConfirmed):
        broker.ensure_mode("live")
    monkeypatch.setenv("CONFIRM_LIVE_TRADING", "true")
    broker.ensure_mode("live")
    assert calls == [True, False]


def test_account_and_sizing(fake_trading_client):
    broker, _ = make_broker(fake_trading_client)
    broker.ensure_mode("paper")
    acc = broker.account()
    assert acc["daily_pnl"] == 1000
    assert AlpacaBroker.position_size(100000, 1, 100, 2, 2) == 250
    assert AlpacaBroker.position_size(1000, 1, 100, 0, 2) == 0


def test_bot_paused(store, provider, fake_trading_client):
    broker, _ = make_broker(fake_trading_client)
    bot = TradingBot(store, provider, broker)
    assert bot.iteration()["status"] == "paused"
    fake_trading_client.submit_order.assert_not_called()


def test_bot_trades_when_enabled(store, provider, fake_trading_client, monkeypatch):
    import main
    monkeypatch.setattr(main, "latest_action", lambda df, p: "buy")
    store.update({"bot_enabled": True, "symbols": ["AAPL"]})
    broker, _ = make_broker(fake_trading_client)
    out = TradingBot(store, provider, broker).iteration()
    assert out["actions"]["AAPL"] == "buy"
    fake_trading_client.submit_order.assert_called_once()
    assert store.trades()[0]["side"] == "buy"


def test_bot_sells_position(store, provider, fake_trading_client, monkeypatch):
    import main
    monkeypatch.setattr(main, "latest_action", lambda df, p: "sell")
    fake_trading_client.get_all_positions.return_value = [SimpleNamespace(
        symbol="AAPL", qty="10", avg_entry_price="100", current_price="110",
        market_value="1100", unrealized_pl="100")]
    store.update({"bot_enabled": True, "symbols": ["AAPL"]})
    broker, _ = make_broker(fake_trading_client)
    assert TradingBot(store, provider, broker).iteration()["actions"]["AAPL"] == "sell"
