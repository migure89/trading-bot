"""Sesiones HTTP con límite de tiempo y reintentos acotados para los clientes REST de alpaca-py.

alpaca-py usa un ``requests.Session`` sin ``timeout``: si el servidor deja de contestar con la
conexión abierta (``Read timed out (read timeout=None)``) la llamada se queda colgada para siempre
y el bot deja de responder incluso a SIGTERM. Aquí se sustituye esa sesión por otra que:

* aplica siempre ``(connect, read)`` por defecto (``ALPACA_HTTP_CONNECT_TIMEOUT``/``ALPACA_HTTP_TIMEOUT``);
* reintenta con backoff exponencial **solo lecturas idempotentes** (GET/HEAD/OPTIONS) ante
  timeouts, cortes de conexión, 429 y 5xx (``ALPACA_HTTP_RETRIES``, ``ALPACA_HTTP_BACKOFF``);
* para POST/DELETE (órdenes, cierres) solo reintenta fallos de *conexión* (la petición no llegó a
  salir); un timeout de lectura en un POST nunca se reintenta a ciegas (ver ``AlpacaBroker``).

También se desactiva el reintento propio de alpaca-py (429/504 para cualquier método, incluido el
envío de órdenes): los GET los cubre urllib3 y los envíos se gestionan con ``client_order_id``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from monitoring.logger import get_logger

log = get_logger("alpaca.http")

DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 20.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 0.5
BACKOFF_MAX = 10.0
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
RETRY_STATUSES = (429, 500, 502, 503, 504)


def _env_number(name: str, default: float, cast=float, minimum: float = 0.0):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        value = None
    if value is None or value < minimum or (cast is float and value != value):  # NaN
        log.warning(f"{name} inválido; se usa el valor por defecto", extra={"value": raw, "default": default})
        return default
    return value


@dataclass(frozen=True)
class HttpConfig:
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    retries: int = DEFAULT_RETRIES
    backoff: float = DEFAULT_BACKOFF

    @property
    def timeout(self) -> tuple[float, float]:
        return (self.connect_timeout, self.read_timeout)

    @classmethod
    def from_env(cls) -> HttpConfig:
        """Lee ``ALPACA_HTTP_TIMEOUT`` (lectura, s), ``ALPACA_HTTP_CONNECT_TIMEOUT`` (s),
        ``ALPACA_HTTP_RETRIES`` y ``ALPACA_HTTP_BACKOFF`` (s). Valores inválidos -> por defecto."""
        return cls(
            connect_timeout=_env_number("ALPACA_HTTP_CONNECT_TIMEOUT", DEFAULT_CONNECT_TIMEOUT, minimum=0.1),
            read_timeout=_env_number("ALPACA_HTTP_TIMEOUT", DEFAULT_READ_TIMEOUT, minimum=0.1),
            retries=_env_number("ALPACA_HTTP_RETRIES", DEFAULT_RETRIES, cast=int),
            backoff=_env_number("ALPACA_HTTP_BACKOFF", DEFAULT_BACKOFF),
        )


class TimeoutSession(requests.Session):
    """``requests.Session`` que pone ``timeout`` por defecto a toda petición sin uno explícito."""

    def __init__(self, timeout: tuple[float, float]) -> None:
        super().__init__()
        self.default_timeout = timeout

    def request(self, method, url, **kwargs):  # noqa: D401 - misma firma que requests
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.default_timeout
        return super().request(method, url, **kwargs)


def make_retry(cfg: HttpConfig) -> Retry:
    """Reintentos de urllib3: conexión para todo método; lectura y 429/5xx solo para GET/HEAD/OPTIONS."""
    return Retry(
        total=cfg.retries,
        connect=cfg.retries,
        read=cfg.retries,
        status=cfg.retries,
        other=0,
        allowed_methods=IDEMPOTENT_METHODS,
        status_forcelist=RETRY_STATUSES,
        backoff_factor=cfg.backoff,
        backoff_max=BACKOFF_MAX,
        respect_retry_after_header=True,
        raise_on_status=False,   # agotados los reintentos, alpaca-py convierte el 4xx/5xx en APIError
    )


def make_session(cfg: HttpConfig | None = None) -> TimeoutSession:
    cfg = cfg or HttpConfig.from_env()
    session = TimeoutSession(cfg.timeout)
    adapter = HTTPAdapter(max_retries=make_retry(cfg))
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def configure_client(client, cfg: HttpConfig | None = None):
    """Aplica timeout y reintentos a un cliente REST de alpaca-py (TradingClient, *HistoricalDataClient).

    Usa el atributo interno ``_session`` de ``alpaca.common.rest.RESTClient`` (cubierto por tests
    con la versión instalada). Devuelve el mismo cliente.
    """
    if not hasattr(client, "_session"):
        raise TypeError(f"{type(client).__name__} no parece un RESTClient de alpaca-py (sin _session)")
    old = client._session
    client._session = make_session(cfg)
    if old is not None and hasattr(old, "close"):
        old.close()
    if hasattr(client, "_retry"):
        client._retry = 0   # sin reintentos propios de alpaca-py (también aplicaba a POST de órdenes)
    return client


def is_transient(exc: BaseException) -> bool:
    """Fallo de red/servidor tras el que no se sabe si la petición tuvo efecto."""
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    return status in RETRY_STATUSES
