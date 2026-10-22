"""Orquestador del bot de trading.

En cada iteración relee la configuración (hot-reload), elige el broker y el
proveedor de datos activos (Alpaca o Interactive Brokers), respeta el kill switch,
descarga datos, calcula la señal y ejecuta órdenes dimensionadas por riesgo.
"""
from __future__ import annotations

import argparse
import signal
import time

from dotenv import load_dotenv

from config.settings import LiveTradingNotConfirmed, SettingsStore, resolve_data_provider
from core.registry import resolve
from data.base import is_crypto
from data.factory import DataProviderRegistry
from execution.base import BaseBroker, BrokerConnectionError
from execution.factory import BrokerRegistry
from monitoring import exporter
from monitoring.logger import get_logger
from strategies.ema_rsi import latest_action
from strategies.indicators import atr

log = get_logger("bot")


class TradingBot:
    """``provider`` y ``broker`` pueden ser una instancia fija o un registro (selección en caliente)."""

    def __init__(self, store: SettingsStore, provider, broker) -> None:
        self.store, self.provider, self.broker = store, provider, broker
        self.running = True

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
                order = broker.market_order(symbol, qty, "buy")
                self.store.record_trade(symbol, "buy", qty, price, cfg["mode"],
                                        str(getattr(order, "id", "")), broker=broker.name)
                exporter.ORDERS.labels(symbol, "buy").inc()
                log.info("orden de compra", extra={"symbol": symbol, "qty": qty, "price": price,
                                                   "broker": broker.name})
                return "buy"
        elif action == "sell" and held > 0:
            order = broker.market_order(symbol, held, "sell")
            self.store.record_trade(symbol, "sell", held, price, cfg["mode"],
                                    str(getattr(order, "id", "")), broker=broker.name)
            exporter.ORDERS.labels(symbol, "sell").inc()
            log.info("orden de venta", extra={"symbol": symbol, "qty": held, "price": price,
                                              "broker": broker.name})
            return "sell"
        return "hold"

    def iteration(self) -> dict:
        cfg = self.store.load()  # hot-reload
        exporter.ITERATIONS.inc()
        broker, provider = self.active(cfg)
        try:
            broker.ensure_mode(cfg["mode"])
        except LiveTradingNotConfirmed as exc:
            log.error(str(exc))
            return {"status": "blocked_live"}
        except BrokerConnectionError as exc:
            exporter.BROKER_CONNECTED.labels(broker.name).set(0)
            exporter.ERRORS.inc()
            log.error("broker no disponible", extra={"broker": broker.name, "error": str(exc)})
            return {"status": "broker_unavailable", "broker": broker.name, "error": str(exc)}
        exporter.BROKER_CONNECTED.labels(broker.name).set(1)
        account = broker.account()
        positions = broker.positions()
        exporter.update_account(account, len(positions), cfg["bot_enabled"], cfg["mode"])
        if not cfg["bot_enabled"]:
            log.info("bot en pausa (kill switch)", extra={"mode": cfg["mode"], "broker": broker.name})
            return {"status": "paused", "broker": broker.name}
        actions = {}
        for symbol in cfg["symbols"]:
            try:
                actions[symbol] = self.process_symbol(symbol, cfg, account["equity"], broker, provider)
            except Exception:
                exporter.ERRORS.inc()
                log.exception("error procesando símbolo", extra={"symbol": symbol})
                actions[symbol] = "error"
        return {"status": "ok", "broker": broker.name, "actions": actions}

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        while self.running:
            try:
                self.iteration()
            except Exception:
                exporter.ERRORS.inc()
                log.exception("error en iteración")
            interval = int(self.store.get("loop_interval_sec", 60))
            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)
        log.info("bot detenido")


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Bot de trading cuantitativo (Alpaca / Interactive Brokers)")
    ap.add_argument("--once", action="store_true", help="ejecuta una sola iteración")
    ap.add_argument("--no-metrics", action="store_true", help="no arranca el exporter Prometheus")
    args = ap.parse_args()
    if not args.no_metrics:
        exporter.start_exporter()
    bot = TradingBot(SettingsStore(), DataProviderRegistry(), BrokerRegistry())
    if args.once:
        print(bot.iteration())
    else:
        bot.run()


if __name__ == "__main__":
    main()
