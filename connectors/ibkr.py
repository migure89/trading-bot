"""Conexión con Interactive Brokers (TWS / IB Gateway) mediante ``ib_async``.

``ib_async`` (sucesor mantenido de ``ib_insync``) es asíncrono y está ligado a un
event loop. Como la API FastAPI atiende peticiones en hilos distintos, esta clase
arranca **un hilo propio con su event loop** y ejecuta allí todas las llamadas
(``IBKRConnection.run``). Así broker y proveedor de datos comparten una única
conexión por proceso, sea cual sea el hilo que los use.

Paper/live se decide por el **puerto**:

=============  =======  ======
Aplicación     Paper    Live
=============  =======  ======
TWS            7497     7496
IB Gateway     4002     4001
Docker (socat) 4004     4003
=============  =======  ======

Además se comprueba que las cuentas de la sesión coincidan con el modo: las
cuentas paper de IBKR empiezan por ``D`` (``DU…``/``DF…``).
"""
from __future__ import annotations

import asyncio
import inspect
import os
import threading
from dataclasses import dataclass, field

from config.settings import LiveTradingNotConfirmed, live_trading_confirmed
from data.base import is_crypto
from execution.base import BrokerConnectionError

KNOWN_PAPER_PORTS = frozenset({7497, 4002, 4004})
KNOWN_LIVE_PORTS = frozenset({7496, 4001, 4003})


class IBKRConnectionError(BrokerConnectionError):
    """No se puede conectar con TWS/IB Gateway o la sesión no es la esperada."""


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "si", "sí"}


@dataclass
class IBKRConfig:
    """Parámetros de conexión leídos de variables de entorno."""

    host: str = "127.0.0.1"
    port_paper: int = 4002
    port_live: int = 4001
    client_id: int = 1
    account: str = ""               # vacío = primera cuenta de la sesión
    readonly: bool = False
    connect_timeout: float = 10.0
    request_timeout: float = 60.0
    stock_exchange: str = "SMART"
    currency: str = "USD"
    crypto_exchange: str = "PAXOS"
    use_rth: bool = False           # True = solo horario regular en históricos
    pacing_sec: float = 1.0         # pausa entre peticiones de históricos troceadas
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> IBKRConfig:
        return cls(
            host=os.getenv("IBKR_HOST", "127.0.0.1"),
            port_paper=int(os.getenv("IBKR_PORT_PAPER", "4002")),
            port_live=int(os.getenv("IBKR_PORT_LIVE", "4001")),
            client_id=int(os.getenv("IBKR_CLIENT_ID", "1")),
            account=os.getenv("IBKR_ACCOUNT", "").strip(),
            readonly=_env_bool("IBKR_READONLY"),
            connect_timeout=float(os.getenv("IBKR_CONNECT_TIMEOUT", "10")),
            request_timeout=float(os.getenv("IBKR_REQUEST_TIMEOUT", "60")),
            stock_exchange=os.getenv("IBKR_STOCK_EXCHANGE", "SMART"),
            currency=os.getenv("IBKR_CURRENCY", "USD"),
            crypto_exchange=os.getenv("IBKR_CRYPTO_EXCHANGE", "PAXOS"),
            use_rth=_env_bool("IBKR_USE_RTH"),
            pacing_sec=float(os.getenv("IBKR_HIST_PACING_SEC", "1.0")),
        )

    def port_for(self, mode: str) -> int:
        """Puerto para el modo pedido, validando que no se crucen paper y live."""
        if mode not in ("paper", "live"):
            raise ValueError(f"Modo inválido: {mode}")
        if mode == "live":
            if not live_trading_confirmed():
                raise LiveTradingNotConfirmed("CONFIRM_LIVE_TRADING=true es obligatorio para live (IBKR)")
            if self.port_live in KNOWN_PAPER_PORTS:
                raise IBKRConnectionError(
                    f"IBKR_PORT_LIVE={self.port_live} es un puerto paper; usa 7496 (TWS), 4001 (Gateway) "
                    "o 4003 (Docker)")
            return self.port_live
        if self.port_paper in KNOWN_LIVE_PORTS:
            raise IBKRConnectionError(
                f"IBKR_PORT_PAPER={self.port_paper} es un puerto live; usa 7497 (TWS), 4002 (Gateway) "
                "o 4004 (Docker)")
        return self.port_paper


def is_paper_account(account: str) -> bool:
    return account.upper().startswith("D")


def check_accounts(mode: str, accounts: list[str], wanted: str = "") -> str:
    """Valida las cuentas de la sesión frente al modo y devuelve la cuenta a usar."""
    if not accounts:
        raise IBKRConnectionError("La sesión de IBKR no devolvió ninguna cuenta")
    if wanted and wanted not in accounts:
        raise IBKRConnectionError(f"IBKR_ACCOUNT={wanted} no está en la sesión ({accounts})")
    account = wanted or accounts[0]
    if mode == "paper" and not is_paper_account(account):
        raise IBKRConnectionError(
            f"Modo paper pero la cuenta {account} parece REAL. Revisa el puerto/sesión de TWS/Gateway.")
    if mode == "live" and is_paper_account(account):
        raise IBKRConnectionError(
            f"Modo live pero la cuenta {account} es de paper trading. Revisa IBKR_PORT_LIVE.")
    return account


def _connect(ib, host: str, port: int, cfg: IBKRConfig):
    return ib.connectAsync(host, port, clientId=cfg.client_id, timeout=cfg.connect_timeout,
                           readonly=cfg.readonly, account=cfg.account)


def _default_ib_factory():
    from ib_async import IB

    return IB()


class IBKRConnection:
    """Conexión única por proceso con TWS/Gateway, con reconexión perezosa."""

    def __init__(self, config: IBKRConfig | None = None, ib_factory=None) -> None:
        self.config = config or IBKRConfig.from_env()
        self._ib_factory = ib_factory or _default_ib_factory
        self._ib = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._contracts: dict[str, object] = {}
        self._pnl_accounts: set[str] = set()
        self.mode: str | None = None
        self.port: int | None = None
        self.account: str | None = None
        self.accounts: list[str] = []
        self.last_error: str | None = None

    # ---- event loop propio -------------------------------------------------
    def _start_loop(self) -> None:
        if self._loop is not None:
            return
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def runner() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        thread = threading.Thread(target=runner, name="ibkr-event-loop", daemon=True)
        thread.start()
        ready.wait()
        self._loop, self._thread = loop, thread

    def run(self, fn, *args, timeout: float | None = None, **kwargs):
        """Ejecuta ``fn`` (síncrona o corrutina) en el hilo de ib_async y devuelve el resultado."""
        self._start_loop()

        async def call():
            res = fn(*args, **kwargs)
            if inspect.isawaitable(res):
                res = await res
            return res

        fut = asyncio.run_coroutine_threadsafe(call(), self._loop)
        return fut.result(timeout or self.config.request_timeout)

    @property
    def ib(self):
        if self._ib is None:
            self._ib = self.run(self._ib_factory)
        return self._ib

    # ---- conexión ----------------------------------------------------------
    def is_connected(self) -> bool:
        if self._ib is None:
            return False
        try:
            return bool(self.run(self._ib.isConnected, timeout=5))
        except Exception:
            return False

    def ensure_connected(self, mode: str):
        """Conecta (o reconecta) en el puerto del modo pedido y devuelve el objeto ``IB``."""
        with self._lock:
            port = self.config.port_for(mode)
            if self.is_connected() and self.mode == mode:
                return self._ib
            if self.is_connected():
                self.disconnect()
            ib = self.ib
            try:
                self.run(_connect, ib, self.config.host, port, self.config,
                         timeout=self.config.connect_timeout + 5)
            except Exception as exc:
                self.last_error = f"No se pudo conectar a {self.config.host}:{port}: {exc}"
                raise IBKRConnectionError(self.last_error) from exc
            try:
                accounts = list(self.run(ib.managedAccounts))
                self.account = check_accounts(mode, accounts, self.config.account)
            except IBKRConnectionError as exc:
                self.last_error = str(exc)
                self.disconnect()
                raise
            self.accounts, self.mode, self.port = accounts, mode, port
            self._pnl_accounts.clear()
            self.last_error = None
            return ib

    def disconnect(self) -> None:
        if self._ib is not None:
            try:
                self.run(self._ib.disconnect, timeout=5)
            except Exception:
                pass
        self.mode = self.port = None

    def status(self) -> dict:
        return {
            "broker": "ibkr",
            "connected": self.is_connected(),
            "mode": self.mode,
            "host": self.config.host,
            "port": self.port,
            "port_paper": self.config.port_paper,
            "port_live": self.config.port_live,
            "client_id": self.config.client_id,
            "account": self.account,
            "accounts": self.accounts,
            "readonly": self.config.readonly,
            "last_error": self.last_error,
        }

    # ---- contratos ---------------------------------------------------------
    def make_contract(self, symbol: str):
        """'AAPL' -> Stock SMART/USD; 'BTC/USD' -> Crypto PAXOS."""
        from ib_async import Crypto, Stock

        if is_crypto(symbol):
            base, quote = symbol.split("/", 1)
            return Crypto(base, self.config.crypto_exchange, quote)
        return Stock(symbol, self.config.stock_exchange, self.config.currency)

    def qualify(self, symbol: str):
        """Resuelve el contrato (conId) en IBKR, con caché."""
        if symbol not in self._contracts:
            contract = self.make_contract(symbol)
            qualified = self.run(self.ib.qualifyContractsAsync, contract)
            if not qualified or qualified[0] is None:
                raise ValueError(f"IBKR no reconoce el símbolo {symbol}")
            self._contracts[symbol] = qualified[0]
        return self._contracts[symbol]

    def daily_pnl(self) -> float | None:
        """PnL diario vía suscripción reqPnL (puede tardar una iteración en llegar)."""
        if not self.account:
            return None
        ib = self.ib
        if self.account not in self._pnl_accounts:
            self.run(ib.reqPnL, self.account)
            self._pnl_accounts.add(self.account)
        for pnl in self.run(ib.pnl, self.account) or []:
            value = getattr(pnl, "dailyPnL", None)
            if value is not None and value == value:  # descarta NaN
                return float(value)
        return None


_shared: IBKRConnection | None = None
_shared_lock = threading.Lock()


def get_shared_connection() -> IBKRConnection:
    """Conexión IBKR compartida por broker y datos dentro del mismo proceso."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = IBKRConnection()
        return _shared
