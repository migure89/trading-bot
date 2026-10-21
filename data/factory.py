"""Selección en caliente del proveedor de datos (Alpaca o IBKR)."""
from __future__ import annotations

from core.registry import Registry

DATA_PROVIDERS = ("alpaca", "ibkr")


def _alpaca():
    from data.alpaca_data import AlpacaDataProvider

    return AlpacaDataProvider()


def _ibkr():
    from data.ibkr_data import IBKRDataProvider

    return IBKRDataProvider()


class DataProviderRegistry(Registry):
    kind = "Proveedor de datos"

    def __init__(self, factories=None) -> None:
        super().__init__(factories or {"alpaca": _alpaca, "ibkr": _ibkr})
