"""Selección en caliente del broker activo (Alpaca o Interactive Brokers)."""
from __future__ import annotations

from core.registry import Registry

BROKERS = ("alpaca", "ibkr")


def _alpaca():
    from execution.broker import AlpacaBroker

    return AlpacaBroker()


def _ibkr():
    from execution.ibkr_broker import IBKRBroker

    return IBKRBroker()


class BrokerRegistry(Registry):
    kind = "Broker"

    def __init__(self, factories=None) -> None:
        super().__init__(factories or {"alpaca": _alpaca, "ibkr": _ibkr})

    def statuses(self) -> dict[str, dict]:
        """Estado de conexión de cada broker (los no usados aún aparecen como no inicializados)."""
        out = {}
        for name in self.available():
            inst = self.instances().get(name)
            if inst is None:
                out[name] = {"broker": name, "connected": False, "initialized": False}
            else:
                try:
                    out[name] = {**inst.connection_status(), "initialized": True}
                except Exception as exc:
                    out[name] = {"broker": name, "connected": False, "initialized": True,
                                 "last_error": str(exc)}
        return out
