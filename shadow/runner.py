"""Estrategias en sombra: calculan señales y pesos objetivo y los registran. **Nunca envían órdenes.**

Garantía en código: este módulo no importa nada de ``execution`` ni recibe un broker; solo
lee barras diarias (proveedor de datos y, para ``dyn_momentum``, el escáner de universo de solo
lectura ``core.universe``) y escribe en la BD (``shadow_signals``), el log JSON y las métricas
Prometheus. Se ejecuta una vez al día aunque el bot esté en pausa.

Varias estrategias a la vez (``shadow_strategies``): cada una se calcula por separado y un fallo
en una (p. ej. el escáner sin tiempo o sin red) se registra, incrementa
``bot_shadow_errors_total{strategy}`` y no impide las demás. Los cierres se descargan una sola
vez por ejecución y se comparten entre estrategias.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

import pandas as pd

from config.settings import SettingsStore, resolve_data_provider, shadow_strategy_list
from core.registry import resolve
from core.schedule import MIN_DAILY_LOOKBACK_DAYS, completed_daily_bars
from data.base import NY
from monitoring import exporter
from monitoring.logger import get_logger
from strategies.portfolio import REGIME_SYMBOL, market_regime
from strategies.registry import StrategySpec, get_strategy, portfolio_weights

log = get_logger("shadow")

SHADOW_TIMEFRAME = "1Day"
EPS = 1e-9


def _changes(prev: pd.Series, last: pd.Series) -> list[dict]:
    out = []
    for sym in last.index.union(prev.index):
        a, b = float(prev.get(sym, 0.0)), float(last.get(sym, 0.0))
        if abs(b - a) <= EPS:
            continue
        action = "entrar" if a <= EPS else "salir" if b <= EPS else "ajustar"
        out.append({"symbol": sym, "from": round(a, 6), "to": round(b, 6), "action": action})
    return out


def _default_scanner():
    from core.universe import scanner_from_env

    return scanner_from_env()


class ShadowRunner:
    """``provider``: instancia o registro de proveedores de datos. No hay broker a propósito.

    ``scanner``: escáner del universo dinámico (``core.universe.UniverseScanner``); se crea al
    primer uso si no se inyecta.
    """

    def __init__(self, store: SettingsStore, provider, scanner=None) -> None:
        self.store, self.provider = store, provider
        self._scanner = scanner

    @property
    def scanner(self):
        if self._scanner is None:
            self._scanner = _default_scanner()
        return self._scanner

    # ---- datos ------------------------------------------------------------------------
    def _closes(self, provider, symbols: list[str], now: datetime, cache: dict) -> tuple[dict, dict]:
        closes, errors = {}, {}
        for sym in symbols:
            if sym not in cache:
                try:
                    df = provider.get_bars(sym, SHADOW_TIMEFRAME, MIN_DAILY_LOOKBACK_DAYS, regular_hours=True)
                    df = completed_daily_bars(df, now)
                    if df is None or df.empty:
                        raise ValueError("sin barras diarias")
                    cache[sym] = df["close"]
                except Exception as exc:  # noqa: BLE001 - un símbolo sin datos no para la sombra
                    cache[sym] = exc
            got = cache[sym]
            if isinstance(got, Exception):
                errors[sym] = str(got) or type(got).__name__
            else:
                closes[sym] = got
        return closes, errors

    # ---- ejecución ----------------------------------------------------------------------
    def run(self, cfg: dict, now: datetime | None = None, only: list[str] | None = None) -> dict:
        """Calcula todas las estrategias en sombra (o solo ``only``) y devuelve un resumen.

        ``{"strategies": {nombre: snapshot}, "errors": {nombre: error}, "orders_sent": 0, ...}``.
        Lanza ``RuntimeError`` solo si no hay ninguna estrategia que calcular.
        """
        now = now or datetime.now(UTC)
        names = list(only) if only is not None else shadow_strategy_list(cfg)
        if not names:
            raise RuntimeError("no hay estrategias en sombra configuradas")
        provider = resolve(self.provider, resolve_data_provider(cfg))
        cache: dict = {}
        summary = {"shadow": True, "orders_sent": 0,
                   "run_at": now.astimezone(UTC).isoformat(timespec="seconds"),
                   "strategies": {}, "errors": {}}
        for name in names:
            t0 = time.monotonic()
            try:
                snap = self.run_strategy(name, cfg, now, provider, cache)
            except Exception as exc:  # noqa: BLE001 - una estrategia no tumba a las demás
                err = str(exc) or type(exc).__name__
                summary["errors"][name] = err
                exporter.SHADOW_ERRORS.labels(name).inc()
                elapsed = round(time.monotonic() - t0, 2)
                log.warning("error en la estrategia en sombra",
                            extra={"strategy": name, "error": err, "elapsed_sec": elapsed})
                continue
            summary["strategies"][name] = snap
        return summary

    def _panel(self, spec: StrategySpec, cfg: dict, now: datetime, provider, cache: dict):
        """(panel de cierres, columnas con peso, errores por símbolo, info del universo)."""
        universe: dict = {}
        if spec.universe == "dynamic" and cfg.get("shadow_universe", "dynamic") == "dynamic":
            panel, stats = self.scanner.closes(now)
            panel = completed_daily_bars(panel, now)
            universe = {"source": "dynamic", **stats}
            exporter.SHADOW_UNIVERSE.labels(spec.name).set(panel.shape[1])
            return panel, list(panel.columns), {}, universe
        symbols = list(spec.symbols) if spec.universe == "own" else list(cfg["shadow_symbols"])
        closes, errors = self._closes(provider, symbols, now, cache)
        if not closes:
            raise RuntimeError(f"sin datos: {errors}")
        panel = pd.DataFrame(closes).sort_index()
        tradable = [s for s in (spec.tradable or symbols) if s in panel.columns]
        if spec.universe == "dynamic":
            universe = {"source": "fixed", "size": len(tradable)}
            exporter.SHADOW_UNIVERSE.labels(spec.name).set(len(tradable))
            panel = panel[tradable]
        elif spec.universe == "shadow":
            panel = panel[tradable]
        return panel, tradable, errors, universe

    def run_strategy(self, name: str, cfg: dict, now: datetime, provider=None,
                     cache: dict | None = None) -> dict:
        spec = get_strategy(name)
        if spec.kind != "portfolio" or spec.trades:
            raise ValueError(f"{name} no puede ejecutarse en sombra")
        provider = provider if provider is not None else resolve(self.provider, resolve_data_provider(cfg))
        cache = {} if cache is None else cache
        panel, tradable, errors, universe = self._panel(spec, cfg, now, provider, cache)
        if panel.empty or not tradable:
            raise RuntimeError("sin cierres para calcular la estrategia")
        regime_closes, regime_err = self._closes(provider, [REGIME_SYMBOL], now, cache)
        errors.update({k: v for k, v in regime_err.items() if spec.universe != "dynamic"})
        regime = market_regime(regime_closes.get(REGIME_SYMBOL, pd.Series(dtype=float)), panel.index)
        weights = portfolio_weights(name, panel, regime).reindex(columns=tradable).fillna(0.0)
        last = weights.iloc[-1]
        prev = weights.iloc[-2] if len(weights) > 1 else last * 0
        components = {}
        if name == "blend":
            components = {c: {s: round(float(w), 6) for s, w in
                              portfolio_weights(c, panel, regime).iloc[-1].items()}
                          for c in ("trend_sma", "rsi2")}
        # Pesos: todos los símbolos con universo fijo; con universo dinámico solo los no nulos.
        shown = last if spec.universe != "dynamic" else last[last.abs() > EPS]
        snapshot = {
            "strategy": name,
            "description": spec.description,
            "shadow": True,
            "trades": False,
            "orders_sent": 0,
            "run_at": now.astimezone(UTC).isoformat(timespec="seconds"),
            "as_of": pd.Timestamp(weights.index[-1]).tz_convert(NY).date().isoformat(),
            "regime_bull": bool(regime.iloc[-1]) if len(regime) else False,
            "weights": {s: round(float(w), 6) for s, w in shown.items()},
            "gross_exposure": round(float(last.sum()), 6),
            "changes": _changes(prev, last),
            "components": components,
            "params": spec.current_params(),
            "symbols": list(spec.symbols) if spec.universe == "own" else [],
            "universe": universe,
            "errors": errors,
        }
        self.store.record_shadow(snapshot)
        exporter.update_shadow(snapshot)
        log.info("señales en sombra (sin órdenes)", extra={"shadow": snapshot})
        return snapshot
