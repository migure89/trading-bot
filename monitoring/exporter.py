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
LAST_SUCCESS = Gauge("bot_last_success_timestamp_seconds",
                     "Momento (epoch) de la última iteración de trading correcta (no avanza en pausa)")
LAST_ITERATION = Gauge("bot_last_iteration_timestamp_seconds",
                       "Momento (epoch) de la última iteración, en pausa o activo (cuelgues)")
LAST_HEALTHCHECK = Gauge("bot_last_healthcheck_timestamp_seconds",
                         "Momento (epoch) del último health-check correcto en pausa")
PAUSED = Gauge("bot_paused", "1 si el bot está en pausa (kill switch)")
INFO = Gauge("bot_info", "Broker y modo configurados (valor siempre 1)", ["broker", "mode"])


def start_exporter(port: int | None = None, addr: str | None = None) -> None:
    start_http_server(int(port or os.getenv("METRICS_PORT", 9100)),
                      addr=addr or os.getenv("METRICS_ADDR", "127.0.0.1"))


def update_config(cfg: dict) -> None:
    """Al principio de cada iteración: estado deseado según la configuración (aunque el broker falle)."""
    BOT_ENABLED.set(int(bool(cfg.get("bot_enabled"))))
    PAUSED.set(int(not cfg.get("bot_enabled")))
    LIVE_MODE.set(int(cfg.get("mode") == "live"))
    INFO.clear()
    INFO.labels(cfg.get("broker", ""), cfg.get("mode", "")).set(1)


def set_broker_connected(broker: str, connected: bool, previous: str | None = None) -> None:
    if previous and previous != broker:
        try:
            BROKER_CONNECTED.remove(previous)   # al cambiar de broker no dejamos la serie antigua
        except KeyError:
            pass
    BROKER_CONNECTED.labels(broker).set(int(connected))


def update_account(account: dict, n_positions: int, enabled: bool | None = None,
                   mode: str | None = None) -> None:
    EQUITY.set(account.get("equity", 0))
    DAILY_PNL.set(account.get("daily_pnl", 0))
    OPEN_POSITIONS.set(n_positions)
    if enabled is not None:
        BOT_ENABLED.set(int(enabled))
    if mode is not None:
        LIVE_MODE.set(int(mode == "live"))


# ---- estrategia en sombra (solo señales; nunca órdenes) -------------------------
SHADOW_WEIGHT = Gauge("bot_shadow_target_weight",
                      "Peso objetivo de la estrategia en sombra (no se opera)", ["strategy", "symbol"])
SHADOW_EXPOSURE = Gauge("bot_shadow_gross_exposure", "Exposición total objetivo de la sombra", ["strategy"])
SHADOW_REGIME = Gauge("bot_shadow_regime_bull", "1 si SPY > SMA200 en la última barra diaria")
SHADOW_LAST_RUN = Gauge("bot_shadow_last_run_timestamp_seconds", "Momento (epoch) de la última sombra")
SHADOW_ERRORS = Counter("bot_shadow_errors_total", "Errores al calcular la estrategia en sombra")


def update_shadow(snapshot: dict) -> None:
    SHADOW_WEIGHT.clear()
    SHADOW_EXPOSURE.clear()
    strategy = snapshot.get("strategy", "")
    for symbol, weight in (snapshot.get("weights") or {}).items():
        SHADOW_WEIGHT.labels(strategy, symbol).set(weight)
    SHADOW_EXPOSURE.labels(strategy).set(snapshot.get("gross_exposure", 0))
    SHADOW_REGIME.set(int(bool(snapshot.get("regime_bull"))))
    SHADOW_LAST_RUN.set_to_current_time()
