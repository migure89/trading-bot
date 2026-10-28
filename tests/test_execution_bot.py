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


# ---- issue #6: precio/cantidad ejecutados y PnL realizado --------------------
def _bot_with_order(store, provider, client, monkeypatch, action, **order):
    import main
    monkeypatch.setattr(main, "latest_action", lambda df, p: action)
    client.get_order_by_id.side_effect = lambda oid: SimpleNamespace(id=oid, **order)
    store.update({"bot_enabled": True, "symbols": ["AAPL"]})
    broker, _ = make_broker(client)
    return TradingBot(store, provider, broker)


def _hold(client, qty="10", entry="100"):
    client.get_all_positions.return_value = [SimpleNamespace(
        symbol="AAPL", qty=qty, avg_entry_price=entry, current_price="110",
        market_value="1100", unrealized_pl="100")]


def test_sell_records_realized_pnl_and_metrics(store, provider, fake_trading_client, monkeypatch):
    from fastapi.testclient import TestClient

    from api import main as api_main

    _hold(fake_trading_client, qty="10", entry="100")
    bot = _bot_with_order(store, provider, fake_trading_client, monkeypatch, "sell",
                          status="filled", filled_qty="10", filled_avg_price="112.5")
    assert bot.iteration()["actions"]["AAPL"] == "sell"
    t = store.trades()[0]
    assert (t["side"], t["qty"], t["price"], t["pnl"], t["status"]) == ("sell", 10, 112.5, 125.0, "filled")

    _hold(fake_trading_client, qty="4", entry="120")      # segunda venta con pérdida
    fake_trading_client.get_order_by_id.side_effect = lambda oid: SimpleNamespace(
        id=oid, status="filled", filled_qty="4", filled_avg_price="110")
    bot.iteration()
    assert store.trades()[0]["pnl"] == -40.0

    api_main.app.dependency_overrides[api_main.get_store] = lambda: store
    try:
        m = TestClient(api_main.app).get("/metrics").json()
    finally:
        api_main.app.dependency_overrides.clear()
    assert m["realized_pnl"] == 85.0 and m["win_rate"] == 0.5
    assert m["profit_factor"] == pytest.approx(125 / 40)


def test_buy_records_fill_price_and_qty(store, provider, fake_trading_client, monkeypatch):
    bot = _bot_with_order(store, provider, fake_trading_client, monkeypatch, "buy",
                          status="partially_filled", filled_qty="3", filled_avg_price="101.25")
    monkeypatch.setenv("ORDER_FILL_TIMEOUT", "0")
    assert bot.iteration()["actions"]["AAPL"] == "buy"
    t = store.trades()[0]
    assert (t["qty"], t["price"], t["pnl"], t["status"]) == (3, 101.25, None, "partially_filled")


@pytest.mark.parametrize("status", ["rejected", "canceled", "expired"])
def test_rejected_orders_are_not_trades(store, provider, fake_trading_client, monkeypatch, status):
    bot = _bot_with_order(store, provider, fake_trading_client, monkeypatch, "buy",
                          status=status, filled_qty="0", filled_avg_price=None)
    out = bot.iteration()
    assert out["actions"]["AAPL"] in ("rejected", "canceled")
    assert store.trades() == []


def test_pending_order_is_recorded_with_status(store, provider, fake_trading_client, monkeypatch):
    monkeypatch.setenv("ORDER_FILL_TIMEOUT", "0")
    bot = _bot_with_order(store, provider, fake_trading_client, monkeypatch, "buy",
                          status="new", filled_qty="0", filled_avg_price=None)
    bot.iteration()
    t = store.trades()[0]
    assert t["status"] == "pending" and t["qty"] > 0 and t["pnl"] is None


def test_ibkr_order_fill_statuses():
    from execution.ibkr_broker import IBKRBroker

    broker = IBKRBroker.__new__(IBKRBroker)
    def order(**st):
        return SimpleNamespace(trade=SimpleNamespace(orderStatus=SimpleNamespace(**st)))
    assert broker.order_fill(order(status="Filled", filled=5, avgFillPrice=10.5)) == {
        "status": "filled", "filled_qty": 5.0, "avg_price": 10.5}
    assert broker.order_fill(order(status="Inactive", filled=0, avgFillPrice=0))["status"] == "rejected"
    assert broker.order_fill(order(status="Submitted", filled=2, avgFillPrice=10))["status"] == \
        "partially_filled"
    assert broker.order_fill(order(status="PreSubmitted", filled=0, avgFillPrice=0))["status"] == "pending"
