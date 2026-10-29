"""Parada limpia del bot con SIGTERM/SIGINT aunque haya una llamada de red en curso.

Antes, el manejador de SIGTERM solo ponía ``running = False``: si el bucle estaba bloqueado en
una petición sin timeout, el proceso no paraba nunca. Ahora:

* La primera señal marca la parada y lanza ``ShutdownRequested`` en el hilo principal, lo que
  interrumpe la espera o la llamada en curso (las peticiones HTTP tienen timeout, así que el
  socket vuelve a Python y la señal se atiende al momento).
* Dentro de ``critical()`` (envío y registro de una orden) la interrupción se aplaza hasta que
  termina el bloque, acotado por los timeouts HTTP y ``ORDER_FILL_TIMEOUT``: no se deja una orden
  enviada sin registrar. Una segunda señal interrumpe incluso eso.

``ShutdownRequested`` hereda de ``BaseException`` para que los ``except Exception`` del bucle no
la traguen.
"""
from __future__ import annotations

import signal
import threading
from contextlib import contextmanager

from monitoring.logger import get_logger

log = get_logger("bot.shutdown")

STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


class ShutdownRequested(BaseException):
    """Parada solicitada por señal."""

    def __init__(self, signum: int | None = None) -> None:
        super().__init__(signal.Signals(signum).name if signum else "shutdown")
        self.signum = signum


class GracefulShutdown:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._critical = 0
        self._signals = 0
        self._previous: dict[int, object] = {}
        self.signum: int | None = None

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def request(self) -> None:
        """Parada sin señal (tests, API interna): el bucle termina en su próxima espera."""
        self._event.set()

    def wait(self, seconds: float) -> bool:
        """Espera ``seconds`` o hasta que se pida la parada. ``True`` si hay que parar."""
        return self._event.wait(max(0.0, seconds))

    # ---- señales -------------------------------------------------------
    def install(self, signals=STOP_SIGNALS) -> bool:
        """Instala los manejadores (solo posible en el hilo principal). Devuelve si se instalaron."""
        if threading.current_thread() is not threading.main_thread():
            return False
        for sig in signals:
            self._previous[sig] = signal.signal(sig, self._handle)
        return True

    def restore(self) -> None:
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)
        self._previous.clear()

    def _handle(self, signum, _frame) -> None:
        self._signals += 1
        self.signum = signum
        self._event.set()
        name = signal.Signals(signum).name
        if self._critical and self._signals == 1:
            log.warning("parada solicitada; se espera a que termine la orden en curso",
                        extra={"signal": name})
            return
        log.info("parada solicitada", extra={"signal": name})
        raise ShutdownRequested(signum)

    @contextmanager
    def critical(self):
        """Bloque que no se interrumpe con la primera señal (envío + registro de una orden)."""
        self._critical += 1
        try:
            yield
        finally:
            self._critical -= 1
        if self._critical == 0 and self.requested:
            raise ShutdownRequested(self.signum)
