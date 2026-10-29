"""Estrategia en sombra: calcula señales y pesos objetivo y los registra. **Nunca envía órdenes.**

Garantía en código: este módulo no importa nada de ``execution`` ni recibe un broker; solo
lee barras diarias del proveedor de datos y escribe en la BD (``shadow_signals``), el log
JSON y las métricas Prometheus. Se ejecuta una vez al día aunque el bot esté en pausa.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from config.settings import SettingsStore, resolve_data_provider
from core.registry import resolve
from core.schedule import MIN_DAILY_LOOKBACK_DAYS, completed_daily_bars
from data.base import NY
from monitoring import exporter
from monitoring.logger import get_logger
from strategies.portfolio import REGIME_SYMBOL, market_regime
from strategies.registry import get_strategy, portfolio_weights

log = get_logger("shadow")

SHADOW_TIMEFRAME = "1Day"
EPS = 1e-9


def _changes(prev: pd.Series, last: pd.Series) -> list[dict]:
    out = []
    for sym in last.index:
        a, b = float(prev.get(sym, 0.0)), float(last[sym])
        if abs(b - a) <= EPS:
            continue
        action = "entrar" if a <= EPS else "salir" if b <= EPS else "ajustar"
        out.append({"symbol": sym, "from": round(a, 6), "to": round(b, 6), "action": action})
    return out


class ShadowRunner:
    """``provider``: instancia o registro de proveedores de datos. No hay broker a propósito."""

    def __init__(self, store: SettingsStore, provider) -> None:
        self.store, self.provider = store, provider

    def run(self, cfg: dict, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        name = cfg.get("shadow_strategy", "off")
        spec = get_strategy(name)
        if spec.kind != "portfolio":
            raise ValueError(f"{name} no puede ejecutarse en sombra")
        provider = resolve(self.provider, resolve_data_provider(cfg))
        symbols = list(cfg["shadow_symbols"])
        fetch = symbols + ([REGIME_SYMBOL] if REGIME_SYMBOL not in symbols else [])
        closes, errors = {}, {}
        for sym in fetch:
            try:
                df = provider.get_bars(sym, SHADOW_TIMEFRAME, MIN_DAILY_LOOKBACK_DAYS, regular_hours=True)
                df = completed_daily_bars(df, now)
                if df is None or df.empty:
                    raise ValueError("sin barras diarias")
                closes[sym] = df["close"]
            except Exception as exc:  # noqa: BLE001 - un símbolo sin datos no para la sombra
                errors[sym] = str(exc) or type(exc).__name__
        if not closes:
            raise RuntimeError(f"sin datos para la sombra: {errors}")
        panel = pd.DataFrame(closes).sort_index()
        tradable = [s for s in symbols if s in panel.columns]
        regime_src = panel[REGIME_SYMBOL] if REGIME_SYMBOL in panel else pd.Series(dtype=float)
        regime = market_regime(regime_src, panel.index)
        weights = portfolio_weights(name, panel[tradable], regime).fillna(0.0)
        last = weights.iloc[-1]
        prev = weights.iloc[-2] if len(weights) > 1 else last * 0
        components = {}
        if name == "blend":
            components = {c: {s: round(float(w), 6) for s, w in
                              portfolio_weights(c, panel[tradable], regime).iloc[-1].items()}
                          for c in ("trend_sma", "rsi2")}
        snapshot = {
            "strategy": name,
            "shadow": True,
            "orders_sent": 0,
            "run_at": now.astimezone(UTC).isoformat(timespec="seconds"),
            "as_of": pd.Timestamp(weights.index[-1]).tz_convert(NY).date().isoformat(),
            "regime_bull": bool(regime.iloc[-1]),
            "weights": {s: round(float(w), 6) for s, w in last.items()},
            "gross_exposure": round(float(last.sum()), 6),
            "changes": _changes(prev, last),
            "components": components,
            "params": spec.params,
            "errors": errors,
        }
        self.store.record_shadow(snapshot)
        exporter.update_shadow(snapshot)
        log.info("señales en sombra (sin órdenes)", extra={"shadow": snapshot})
        return snapshot
