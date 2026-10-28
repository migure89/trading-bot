"""Orquestador del bot de trading.

En cada iteración relee la configuración (hot-reload), elige el broker y el
proveedor de datos activos (Alpaca o Interactive Brokers), respeta el kill switch,
descarga datos, calcula la señal y ejecuta órdenes dimensionadas por riesgo.
"""
from __future__ import annotations

import argparse
import json
import signal
import time

from config.settings import LiveTradingNotConfirmed, SettingsStore, resolve_data_provider
from core.registry import resolve
from data.base import is_crypto
from data.factory import DataProviderRegistry
from execution.base import CANCELED, FILLED, PARTIAL, REJECTED, BaseBroker
from execution.factory import BrokerRegistry
from monitoring import exporter
from monitoring.logger import get_logger, install_excepthook
from strategies.ema_rsi import latest_action
from strategies.indicators import atr

log = get_logger("bot")

BACKOFF_MAX_SEC = 300  # tope del backoff exponencial ante fallos del broker


class TradingBot:
    """``provider`` y ``broker`` pueden ser una instancia fija o un registro (selección en caliente)."""

    def __init__(self, store: SettingsStore, provider, broker) -> None:
        self.store, self.provider, self.broker = store, provider, broker
        self.running = True
        self._paused: bool | None = None           # para registrar la pausa solo al cambiar
        self._failures = 0                          # fallos consecutivos del broker (backoff)
        self._last_error: tuple[str, str] | None = None
        self._last_broker: str | None = None

    def active(self, cfg: dict) -> tuple[BaseBroker, object]:
        return resolve(self.broker, cfg["broker"]), resolve(self.provider, resolve_data_provider(cfg))

    def process_symbol(self, symbol: str, cfg: dict, equity: float,
                       broker: BaseBroker | None = None, provider=None) -> str:
        if broker is None or provider is None:
            broker, provider = self.active(cfg)
        params = cfg["strategy_params"]
        df = provider.get_bars(symbol, cfg["timeframe"], cfg["lookback_days"])
        action = latest_action(df, params)
        held = broker.position_qty(symbol)
        price = float(df["close"].iloc[-1]) if len(df) else 0.0

        if action == "buy" and held <= 0:
            atr_v = float(atr(df["high"], df["low"], df["close"], int(params["atr_period"])).iloc[-1])
            qty = broker.position_size(equity, cfg["risk_pct"], price, atr_v,
                                       float(params["atr_stop_mult"]), is_crypto(symbol))
            if qty > 0:
                return self.execute(symbol, "buy", qty, price, cfg, broker)
        elif action == "sell" and held > 0:
            pos = broker.position(symbol) or {}
            return self.execute(symbol, "sell", held, price, cfg, broker,
                                entry_price=pos.get("avg_entry_price"))
        return "hold"

    def execute(self, symbol: str, side: str, qty: float, ref_price: float, cfg: dict,
                broker: BaseBroker, entry_price: float | None = None) -> str:
        """Envía la orden, espera su ejecución y registra precio/cantidad reales y el PnL.

        Las órdenes rechazadas o canceladas sin ejecutar no se registran como trades.
        """
        order = broker.market_order(symbol, qty, side)
        fill = broker.wait_fill(order)
        order_id = str(getattr(order, "id", ""))
        if fill["status"] in (REJECTED, CANCELED):
            exporter.ERRORS.inc()
            log.warning("orden no ejecutada", extra={"symbol": symbol, "side": side, "qty": qty,
                                                     "status": fill["status"], "order_id": order_id,
                                                     "broker": broker.name})
            return fill["status"]
        executed = fill["filled_qty"] if fill["status"] in (FILLED, PARTIAL) and fill["filled_qty"] else qty
        price = fill["avg_price"] or ref_price
        pnl = None
        if side == "sell" and fill["status"] in (FILLED, PARTIAL) and fill["avg_price"] and entry_price:
            # PnL realizado = (precio de venta - precio medio de entrada) * cantidad vendida
            pnl = round((fill["avg_price"] - float(entry_price)) * executed, 6)
        self.store.record_trade(symbol, side, executed, price, cfg["mode"], order_id, pnl=pnl,
                                broker=broker.name, status=fill["status"])
        exporter.ORDERS.labels(symbol, side).inc()
        log.info(f"orden de {'compra' if side == 'buy' else 'venta'}",
                 extra={"symbol": symbol, "qty": executed, "price": price, "status": fill["status"],
                        "pnl": pnl, "broker": broker.name})
        return side

    def iteration(self) -> dict:
        cfg = self.store.load()  # hot-reload
        exporter.ITERATIONS.inc()
        exporter.update_config(cfg)          # bot_enabled / bot_live_mode / bot_info, pase lo que pase
        if not cfg["bot_enabled"]:
            # En pausa no se toca el broker (ni conexiones ni llamadas de trading).
            if self._paused is not True:
                log.info("bot en pausa (kill switch)", extra={"mode": cfg["mode"], "broker": cfg["broker"]})
            self._paused = True
            exporter.LAST_SUCCESS.set_to_current_time()
            return {"status": "paused", "broker": cfg["broker"]}
        if self._paused:
            log.info("bot reanudado", extra={"mode": cfg["mode"], "broker": cfg["broker"]})
        self._paused = False

        broker, provider = self.active(cfg)
        try:
            broker.ensure_mode(cfg["mode"])
            account = broker.account()
            positions = broker.positions()
        except LiveTradingNotConfirmed as exc:
            self._broker_failed(broker.name, str(exc))
            return {"status": "blocked_live"}
        except Exception as exc:  # noqa: BLE001 - cualquier fallo del broker (red, claves, Gateway…)
            self._broker_failed(broker.name, str(exc) or type(exc).__name__)
            return {"status": "broker_unavailable", "broker": broker.name, "error": str(exc)}
        self._broker_ok(broker.name)
        exporter.update_account(account, len(positions))
        actions = {}
        for symbol in cfg["symbols"]:
            try:
                actions[symbol] = self.process_symbol(symbol, cfg, account["equity"], broker, provider)
            except Exception:
                exporter.ERRORS.inc()
                log.exception("error procesando símbolo", extra={"symbol": symbol})
                actions[symbol] = "error"
        exporter.LAST_SUCCESS.set_to_current_time()
        return {"status": "ok", "broker": broker.name, "actions": actions}

    # ---- estado de conexión: deduplicación de logs y backoff --------------
    def _broker_failed(self, name: str, error: str) -> None:
        exporter.set_broker_connected(name, False, self._last_broker)
        exporter.ERRORS.inc()
        self._failures += 1
        key = (name, error)
        if key != self._last_error:          # primer fallo o error distinto: un único ERROR
            log.error("broker no disponible", extra={"broker": name, "error": error})
        else:
            log.debug("broker sigue sin responder", extra={"broker": name, "failures": self._failures})
        self._last_error, self._last_broker = key, name

    def _broker_ok(self, name: str) -> None:
        if self._failures:
            log.info("broker reconectado", extra={"broker": name, "failures": self._failures})
        exporter.set_broker_connected(name, True, self._last_broker)
        self._failures, self._last_error, self._last_broker = 0, None, name

    def next_delay(self, interval: int) -> int:
        """Espera hasta la siguiente iteración: backoff exponencial (máx. 5 min) si el broker falla."""
        if not self._failures:
            return interval
        return int(min(max(interval, 5) * 2 ** (self._failures - 1), max(BACKOFF_MAX_SEC, interval)))

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        while self.running:
            try:
                self.iteration()
            except Exception:
                exporter.ERRORS.inc()
                log.exception("error en iteración")
            interval = self.next_delay(int(self.store.get("loop_interval_sec", 60)))
            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)
        log.info("bot detenido")


def main() -> None:
    install_excepthook("bot")
    ap = argparse.ArgumentParser(description="Bot de trading cuantitativo (Alpaca / Interactive Brokers)")
    ap.add_argument("--once", action="store_true", help="ejecuta una sola iteración")
    ap.add_argument("--no-metrics", action="store_true", help="no arranca el exporter Prometheus")
    args = ap.parse_args()
    if not args.no_metrics:
        exporter.start_exporter()
    bot = TradingBot(SettingsStore(), DataProviderRegistry(), BrokerRegistry())
    if args.once:
        print(json.dumps(bot.iteration(), default=str))
    else:
        bot.run()


if __name__ == "__main__":
    main()
