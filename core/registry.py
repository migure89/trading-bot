"""Registro genérico de implementaciones intercambiables (brokers y proveedores de datos).

Cada implementación se instancia de forma perezosa la primera vez que se pide y se
reutiliza después, de modo que el bot puede cambiar de broker en caliente sin
recrear conexiones en cada iteración.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


class Registry:
    """Diccionario ``nombre -> factoría`` con caché de instancias."""

    kind = "implementación"

    def __init__(self, factories: dict[str, Callable[[], Any]]) -> None:
        self._factories = dict(factories)
        self._instances: dict[str, Any] = {}

    def available(self) -> list[str]:
        return list(self._factories)

    def get(self, name: str) -> Any:
        name = str(name).lower()
        if name not in self._factories:
            raise ValueError(f"{self.kind} desconocido: {name} (disponibles: {self.available()})")
        if name not in self._instances:
            self._instances[name] = self._factories[name]()
        return self._instances[name]

    def instances(self) -> dict[str, Any]:
        """Instancias ya creadas (no fuerza la creación de las demás)."""
        return dict(self._instances)


def resolve(obj: Any, name: str) -> Any:
    """Devuelve ``obj.get(name)`` si ``obj`` es un registro; si no, el propio objeto."""
    return obj.get(name) if isinstance(obj, Registry) else obj
