"""Timeouts HTTP de Alpaca, reintentos acotados, órdenes sin duplicar y parada por señal."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
from alpaca.common.exceptions import APIError

from core.alpaca_http import HttpConfig, TimeoutSession, configure_client, is_transient, make_session
from core.shutdown import GracefulShutdown, ShutdownRequested
from execution.base import BrokerConnectionError
from execution.broker import AlpacaBroker, OrderStatusUnknown

TIMEOUT_ERRORS = (requests.Timeout, requests.ConnectionError)
ROOT = Path(__file__).resolve().parents[1]


# ---- servidor HTTP local que acepta conexiones y nunca responde ----------------
class SilentServer:
    def __init__(self, respond: bytes | None = None) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        self.respond = respond
        self._conns: list[socket.socket] = []
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v2/account"

    def _loop(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            self._conns.append(conn)
            if self.respond:
                conn.recv(65536)
                conn.sendall(self.respond)
                conn.close()

    def close(self) -> None:
        self._stop = True
        self.sock.close()
        for c in self._conns:
            c.close()


@pytest.fixture
def silent():
    srv = SilentServer()
    yield srv
    srv.close()


def fast(retries: int = 0) -> HttpConfig:
    return HttpConfig(connect_timeout=1.0, read_timeout=0.3, retries=retries, backoff=0.0)


# ---- configuración ----------------------------------------------------------
def test_config_from_env(monkeypatch):
    for k in ("ALPACA_HTTP_TIMEOUT", "ALPACA_HTTP_CONNECT_TIMEOUT",
              "ALPACA_HTTP_RETRIES", "ALPACA_HTTP_BACKOFF"):
        monkeypatch.delenv(k, raising=False)
    assert HttpConfig.from_env() == HttpConfig()
    assert HttpConfig().timeout == (5.0, 20.0)
    monkeypatch.setenv("ALPACA_HTTP_TIMEOUT", "7.5")
    monkeypatch.setenv("ALPACA_HTTP_CONNECT_TIMEOUT", "2")
    monkeypatch.setenv("ALPACA_HTTP_RETRIES", "1")
    monkeypatch.setenv("ALPACA_HTTP_BACKOFF", "0")
    assert HttpConfig.from_env() == HttpConfig(2.0, 7.5, 1, 0.0)
    monkeypatch.setenv("ALPACA_HTTP_TIMEOUT", "nada")
    monkeypatch.setenv("ALPACA_HTTP_RETRIES", "-3")
    cfg = HttpConfig.from_env()
    assert cfg.read_timeout == 20.0 and cfg.retries == 3


def test_session_sets_default_timeout_but_keeps_explicit(monkeypatch):
    seen = []
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, **kw: seen.append(kw["timeout"]))
    s = TimeoutSession((1.0, 2.0))
    s.request("GET", "http://x")
    s.request("GET", "http://x", timeout=9)
    assert seen == [(1.0, 2.0), 9]


def test_read_timeout_is_bounded(silent):
    t0 = time.monotonic()
    with pytest.raises(TIMEOUT_ERRORS):   # sin reintentos urllib3 lo envuelve en ConnectionError
        make_session(fast()).get(silent.url)
    assert time.monotonic() - t0 < 3


def test_get_is_retried_with_limit(silent):
    with pytest.raises(requests.ConnectionError):  # MaxRetryError(ReadTimeoutError)
        make_session(fast(retries=2)).get(silent.url)
    time.sleep(0.1)
    assert silent.connections == 3   # 1 + 2 reintentos, ni uno más


def test_post_read_timeout_is_never_retried(silent):
    with pytest.raises(requests.ReadTimeout):
        make_session(fast(retries=3)).post(silent.url, json={"symbol": "AAPL"})
    time.sleep(0.1)
    assert silent.connections == 1


def test_get_retries_5xx_then_returns_response():
    srv = SilentServer(respond=b"HTTP/1.1 503 Service Unavailable\r\n"
                               b"Content-Length: 0\r\nConnection: close\r\n\r\n")
    try:
        r = make_session(fast(retries=2)).get(srv.url)
        assert r.status_code == 503 and srv.connections == 3
    finally:
        srv.close()


def test_configure_real_alpaca_clients():
    from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
    from alpaca.trading.client import TradingClient

    for client in (TradingClient("k", "s", paper=True), StockHistoricalDataClient("k", "s"),
                   CryptoHistoricalDataClient()):
        configure_client(client, fast())
        assert isinstance(client._session, TimeoutSession)
        assert client._session.default_timeout == (1.0, 0.3)
        assert client._retry == 0   # sin reintento propio de alpaca-py (también aplicaba a POST)
    with pytest.raises(TypeError):
        configure_client(object())


def test_real_trading_client_times_out(silent):
    from alpaca.trading.client import TradingClient

    client = configure_client(TradingClient("k", "s", paper=True, url_override=f"http://127.0.0.1:{silent.port}"),
                              fast())
    t0 = time.monotonic()
    with pytest.raises(TIMEOUT_ERRORS):
        client.get_account()
    assert time.monotonic() - t0 < 3


def test_factories_configure_clients(monkeypatch):
    from data.alpaca_data import AlpacaDataProvider

    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")
    assert isinstance(AlpacaBroker._default_factory(paper=True)._session, TimeoutSession)
    p = AlpacaDataProvider("k", "s")
    for c in (p.stock_client, p.crypto_client, p.calendar_client):
        assert isinstance(c._session, TimeoutSession)


def _api_error(status: int) -> APIError:
    resp = SimpleNamespace(status_code=status)
    return APIError('{"code": 1, "message": "x"}', SimpleNamespace(response=resp, request=None))


def test_is_transient():
    assert is_transient(requests.ReadTimeout())
    assert is_transient(requests.ConnectionError())
    assert is_transient(_api_error(503)) and is_transient(_api_error(429))
    assert not is_transient(_api_error(403)) and not is_transient(_api_error(404))
    assert not is_transient(ValueError())


# ---- órdenes: nunca duplicar ----------------------------------------------------
@pytest.fixture
def broker(fake_trading_client, monkeypatch):
    monkeypatch.delenv("ALPACA_ORDER_SUBMIT_ATTEMPTS", raising=False)
    b = AlpacaBroker(client_factory=lambda paper: fake_trading_client, sleep=lambda s: None)
    b.ensure_mode("paper")
    return b, fake_trading_client


def _coids(client) -> list[str]:
    return [c.kwargs["order_data"].client_order_id for c in client.submit_order.call_args_list]


def test_order_carries_client_order_id(broker):
    b, client = broker
    b.market_order("AAPL", 1, "buy")
    (coid,) = _coids(client)
    assert coid.startswith("tb-")
    client.get_order_by_client_id.assert_not_called()


def test_submit_timeout_but_order_exists_is_not_resent(broker):
    b, client = broker
    client.submit_order.side_effect = requests.ReadTimeout("read timeout")
    client.get_order_by_client_id.side_effect = lambda coid: SimpleNamespace(id="o-7", client_order_id=coid)
    order = b.market_order("AAPL", 1, "buy")
    assert order.id == "o-7"
    assert client.submit_order.call_count == 1
    assert client.get_order_by_client_id.call_args.args[0] == _coids(client)[0]


def test_submit_timeout_and_not_found_resends_same_id(broker):
    b, client = broker
    client.submit_order.side_effect = [requests.ReadTimeout(), SimpleNamespace(id="o-8")]
    client.get_order_by_client_id.side_effect = _api_error(404)
    assert b.market_order("AAPL", 1, "buy").id == "o-8"
    first, second = _coids(client)
    assert first == second   # mismo client_order_id: Alpaca rechazaría un duplicado


def test_submit_gives_up_after_attempts(broker, monkeypatch):
    b, client = broker
    monkeypatch.setenv("ALPACA_ORDER_SUBMIT_ATTEMPTS", "3")
    client.submit_order.side_effect = _api_error(503)
    client.get_order_by_client_id.side_effect = _api_error(404)
    with pytest.raises(BrokerConnectionError):
        b.market_order("AAPL", 1, "buy")
    assert client.submit_order.call_count == 3 and len(set(_coids(client))) == 1


def test_unknown_order_state_never_resends(broker):
    b, client = broker
    client.submit_order.side_effect = requests.ReadTimeout()
    client.get_order_by_client_id.side_effect = requests.ConnectionError("sin red")
    with pytest.raises(OrderStatusUnknown):
        b.market_order("AAPL", 1, "buy")
    assert client.submit_order.call_count == 1


def test_clear_rejection_is_not_retried(broker):
    b, client = broker
    client.submit_order.side_effect = _api_error(403)
    with pytest.raises(APIError):
        b.market_order("AAPL", 1, "buy")
    assert client.submit_order.call_count == 1
    client.get_order_by_client_id.assert_not_called()


# ---- parada por señal -------------------------------------------------------
def _signal_later(sig=signal.SIGTERM, delay=0.2) -> threading.Timer:
    t = threading.Timer(delay, os.kill, (os.getpid(), sig))
    t.start()
    return t


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signal_interrupts_blocking_call(sig):
    sd = GracefulShutdown()
    assert sd.install()
    try:
        _signal_later(sig)
        t0 = time.monotonic()
        with pytest.raises(ShutdownRequested):
            time.sleep(10)   # equivale a una llamada de red en curso
        assert time.monotonic() - t0 < 2 and sd.requested
    finally:
        sd.restore()
    assert signal.getsignal(signal.SIGTERM) is not sd._handle


def test_signal_waits_for_critical_section():
    sd = GracefulShutdown()
    sd.install()
    done = []
    try:
        _signal_later(delay=0.1)
        with pytest.raises(ShutdownRequested):
            with sd.critical():
                time.sleep(0.5)       # la orden en curso termina...
                done.append(True)     # ...y se registra
        assert done == [True]
    finally:
        sd.restore()


def test_second_signal_forces_exit_from_critical_section():
    sd = GracefulShutdown()
    sd.install()
    try:
        _signal_later(delay=0.1)
        _signal_later(delay=0.3)
        t0 = time.monotonic()
        with pytest.raises(ShutdownRequested):
            with sd.critical():
                time.sleep(10)
        assert time.monotonic() - t0 < 2
    finally:
        sd.restore()


def test_bot_run_stops_on_sigterm_during_hung_call(store):
    from main import TradingBot

    store.update({"bot_enabled": True})
    provider = MagicMock()
    hung = MagicMock()
    hung.name = "alpaca"
    hung.account.side_effect = lambda: time.sleep(30)   # broker colgado
    bot = TradingBot(store, provider, hung, healthcheck_interval=0)
    _signal_later(delay=0.3)
    t0 = time.monotonic()
    bot.run()
    assert time.monotonic() - t0 < 3
    assert bot.running is False
    assert signal.getsignal(signal.SIGTERM) != bot.shutdown._handle   # manejador restaurado


def test_bot_run_stops_during_sleep(store):
    from main import TradingBot

    bot = TradingBot(store, MagicMock(), MagicMock(), healthcheck_interval=0)
    store.update({"loop_interval_sec": 3600})
    _signal_later(delay=0.3)
    t0 = time.monotonic()
    bot.run()
    assert time.monotonic() - t0 < 3


def test_order_in_flight_is_recorded_before_stopping(store, fake_trading_client):
    from main import TradingBot

    def slow_submit(order_data):
        time.sleep(0.5)
        return SimpleNamespace(id="o-1")

    fake_trading_client.submit_order.side_effect = slow_submit
    b = AlpacaBroker(client_factory=lambda paper: fake_trading_client)
    b.ensure_mode("paper")
    bot = TradingBot(store, MagicMock(), b, healthcheck_interval=0)
    bot.shutdown.install()
    try:
        _signal_later(delay=0.1)
        with pytest.raises(ShutdownRequested):
            bot.execute("AAPL", "buy", 1, 100.0, store.load(), b)
    finally:
        bot.shutdown.restore()
    assert fake_trading_client.submit_order.call_count == 1
    assert [t["order_id"] for t in store.trades()] == ["o-1"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_main_process_exits_on_sigterm_and_frees_metrics_port(tmp_path):
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("APCA_")}
    env.update(SKIP_DOTENV="1", SETTINGS_DB_PATH=str(tmp_path / "s.db"), METRICS_PORT=str(port),
               PAUSED_HEALTHCHECK_SECONDS="0", CONFIRM_LIVE_TRADING="false")
    proc = subprocess.Popen([sys.executable, "main.py"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                requests.get(f"http://127.0.0.1:{port}/metrics", timeout=1)
                break
            except requests.ConnectionError:
                time.sleep(0.2)
        else:
            pytest.fail("el exporter no arrancó: " + proc.stdout.read1().decode(errors="replace"))
        t0 = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
        assert time.monotonic() - t0 < 5
        out = proc.stdout.read().decode()
        assert "bot detenido" in out
        with socket.socket() as s:   # puerto liberado (ignora TIME_WAIT, no un listener activo)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
    finally:
        if proc.poll() is None:
            proc.kill()


def test_ibkr_call_timeout_cancels_coroutine():
    import asyncio
    from concurrent.futures import TimeoutError as FutureTimeout

    from connectors.ibkr import IBKRConfig, IBKRConnection

    conn = IBKRConnection(IBKRConfig(), ib_factory=MagicMock)
    cancelled = threading.Event()

    async def hung():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    t0 = time.monotonic()
    with pytest.raises((FutureTimeout, TimeoutError)):
        conn.run(hung, timeout=0.2)
    assert time.monotonic() - t0 < 2
    assert cancelled.wait(2)


def test_main_sigterm_during_startup_exits_cleanly(monkeypatch):
    """Un SIGTERM antes de que arranque el bucle (exporter arrancando) no mata el proceso."""
    import main as main_mod

    stopped = []

    def fake_start():
        os.kill(os.getpid(), signal.SIGTERM)
        return "handle"

    monkeypatch.setattr(main_mod.exporter, "start_exporter", fake_start)
    monkeypatch.setattr(main_mod.exporter, "stop_exporter", stopped.append)
    monkeypatch.setattr(main_mod, "TradingBot", MagicMock(side_effect=AssertionError("no debe construirse")))
    monkeypatch.setattr(sys, "argv", ["main.py"])
    before = signal.getsignal(signal.SIGTERM)
    main_mod.main()
    assert stopped == ["handle"]
    assert signal.getsignal(signal.SIGTERM) is before
