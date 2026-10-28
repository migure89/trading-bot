"""Exporter Prometheus con métricas del bot.

Escucha en ``METRICS_ADDR:METRICS_PORT`` (por defecto ``127.0.0.1:9100``). En Docker se usa
``METRICS_ADDR=0.0.0.0`` dentro del contenedor y el puerto se publica solo en localhost del host.
"""
from __future__ import annotations

import os

from prometheus_client import Counter, Gauge, start_http_server

EQUITY = Gauge("bot_equity_usd", "Equity de la cuenta del broker activo")
DAILY_PNL = Gauge("bot_daily_pnl_usd", "PnL diario")
OPEN_POSITIONS = Gauge("bot_open_positions", "Número de posiciones abiertas")
BOT_ENABLED = Gauge("bot_enabled", "1 si el bot está activo")
LIVE_MODE = Gauge("bot_live_mode", "1 si opera en real")
BROKER_CONNECTED = Gauge("bot_broker_connected", "1 si el broker responde", ["broker"])
ORDERS = Counter("bot_orders_total", "Órdenes enviadas", ["symbol", "side"])
ERRORS = Counter("bot_errors_total", "Errores en el bucle principal")
ITERATIONS = Counter("bot_iterations_total", "Iteraciones del bucle principal")


def start_exporter(port: int | None = None, addr: str | None = None) -> None:
    start_http_server(int(port or os.getenv("METRICS_PORT", 9100)),
                      addr=addr or os.getenv("METRICS_ADDR", "127.0.0.1"))


def update_account(account: dict, n_positions: int, enabled: bool, mode: str) -> None:
    EQUITY.set(account.get("equity", 0))
    DAILY_PNL.set(account.get("daily_pnl", 0))
    OPEN_POSITIONS.set(n_positions)
    BOT_ENABLED.set(int(enabled))
    LIVE_MODE.set(int(mode == "live"))
