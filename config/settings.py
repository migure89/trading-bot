"""Configuración dinámica con hot-reload sobre SQLite.

El motor de trading llama a ``SettingsStore.load()`` en cada iteración, de modo
que cualquier cambio hecho desde la API, el dashboard o el optimizador se aplica
sin reiniciar el proceso.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.env import load_env
from data.base import TIMEFRAMES

load_env()  # antes de leer SETTINGS_DB_PATH, CONFIRM_LIVE_TRADING, etc.

BASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = BASE_DIR / "schema.sql"
DEFAULT_DB_PATH = BASE_DIR / "settings.db"


def default_db_path() -> Path:
    """Ruta de la BD resuelta al crear el store (no al importar): respeta SETTINGS_DB_PATH."""
    return Path(os.getenv("SETTINGS_DB_PATH") or DEFAULT_DB_PATH)

DEFAULT_SETTINGS: dict[str, Any] = {
    "mode": "paper",              # paper | live
    "broker": "alpaca",           # alpaca | ibkr  (broker activo, conmutable en caliente)
    "data_provider": "auto",      # auto (= broker activo) | alpaca | ibkr
    "bot_enabled": False,         # kill switch: False = pausado
    "symbols": ["AAPL", "MSFT"],  # activos (acciones o cripto tipo "BTC/USD")
    "risk_pct": 1.0,              # % del equity arriesgado por operación
    "timeframe": "1Hour",         # 1Min | 5Min | 15Min | 1Hour | 1Day
    "loop_interval_sec": 60,
    "lookback_days": 30,
    "strategy_params": {
        "ema_fast": 12,
        "ema_slow": 26,
        "rsi_period": 14,
        "rsi_overbought": 70,
        "rsi_oversold": 30,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "bb_period": 20,
        "bb_std": 2.0,
    },
}

# Límites compartidos por la API, el dashboard y el store.
RISK_PCT_MIN, RISK_PCT_MAX = 0.0, 10.0     # risk_pct en (0, 10]
LOOKBACK_DAYS_MAX = 3650
LOOP_INTERVAL_MIN, LOOP_INTERVAL_MAX = 5, 86_400
# Acciones (AAPL, BRK.B, BRK-B) o cripto (BTC/USD)
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,10}([./-][A-Z0-9]{1,10})?$")

VALID_MODES = {"paper", "live"}
VALID_BROKERS = {"alpaca", "ibkr"}
VALID_DATA_PROVIDERS = {"auto", *VALID_BROKERS}


class StrategyParams(BaseModel):
    """Parámetros de la estrategia EMA/RSI/ATR/Bollinger (todas las claves conocidas)."""

    model_config = ConfigDict(extra="forbid")

    ema_fast: int = Field(ge=1, le=500)
    ema_slow: int = Field(ge=2, le=1000)
    rsi_period: int = Field(ge=2, le=200)
    rsi_overbought: float = Field(gt=0, lt=100)
    rsi_oversold: float = Field(gt=0, lt=100)
    atr_period: int = Field(ge=1, le=200)
    atr_stop_mult: float = Field(gt=0, le=20)
    bb_period: int = Field(ge=2, le=500)
    bb_std: float = Field(gt=0, le=10)

    def check(self) -> StrategyParams:
        if self.ema_fast >= self.ema_slow:
            raise ValueError("strategy_params: ema_fast debe ser menor que ema_slow")
        if self.rsi_oversold >= self.rsi_overbought:
            raise ValueError("strategy_params: rsi_oversold debe ser menor que rsi_overbought")
        return self


STRATEGY_KEYS = tuple(StrategyParams.model_fields)


def validate_strategy_params(params: dict[str, Any]) -> dict[str, Any]:
    """Valida el conjunto completo de parámetros y devuelve los valores normalizados."""
    try:
        model = StrategyParams(**params)
    except ValidationError as exc:
        errs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise ValueError(f"strategy_params inválido: {errs}") from None
    return model.check().model_dump()


def normalize_symbols(symbols: Any) -> list[str]:
    """Lista no vacía, sin cadenas vacías, en mayúsculas y sin duplicados (conserva el orden)."""
    if isinstance(symbols, str) or not isinstance(symbols, list | tuple):
        raise ValueError("symbols debe ser una lista")
    out: list[str] = []
    for raw in symbols:
        sym = str(raw).strip().upper()
        if not sym:
            raise ValueError("symbols no puede contener cadenas vacías")
        if not SYMBOL_RE.match(sym):
            raise ValueError(f"Símbolo inválido: {raw!r}")
        if sym not in out:
            out.append(sym)
    if not out:
        raise ValueError("symbols no puede estar vacío")
    return out


class LiveTradingNotConfirmed(PermissionError):
    """Se intenta pasar a live sin CONFIRM_LIVE_TRADING=true."""


def live_trading_confirmed() -> bool:
    return os.getenv("CONFIRM_LIVE_TRADING", "false").strip().lower() == "true"


def resolve_data_provider(cfg: dict[str, Any]) -> str:
    """Nombre del proveedor de datos efectivo ("auto" = el del broker activo)."""
    provider = cfg.get("data_provider", "auto")
    return cfg.get("broker", "alpaca") if provider == "auto" else provider


class SettingsStore:
    """Acceso thread-safe a la configuración persistida en SQLite."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or default_db_path())
        self._lock = threading.Lock()
        self.init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        """Crea la BD (si no existe) y siembra los valores por defecto."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._conn() as conn:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            # Migración: BDs creadas antes del soporte multi-broker no tienen trades.broker
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(trades)")}
            if "broker" not in cols:
                conn.execute("ALTER TABLE trades ADD COLUMN broker TEXT")
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )

    # ---- lectura / escritura -------------------------------------------
    def load(self) -> dict[str, Any]:
        """Devuelve la configuración completa (se llama en cada iteración)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        data = {**DEFAULT_SETTINGS}
        data.update({r["key"]: json.loads(r["value"]) for r in rows})
        return data

    def get(self, key: str, default: Any = None) -> Any:
        return self.load().get(key, default)

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Actualiza claves validando reglas de seguridad."""
        changes = dict(changes)
        if "mode" in changes:
            mode = str(changes["mode"]).lower()
            if mode not in VALID_MODES:
                raise ValueError(f"Modo inválido: {mode}")
            if mode == "live" and not live_trading_confirmed():
                raise LiveTradingNotConfirmed(
                    "Para operar en real exporta CONFIRM_LIVE_TRADING=true"
                )
            changes["mode"] = mode
        if "broker" in changes:
            broker = str(changes["broker"]).lower()
            if broker not in VALID_BROKERS:
                raise ValueError(f"Broker inválido: {broker}")
            target_mode = changes.get("mode") or self.get("mode")
            if target_mode == "live" and not live_trading_confirmed():
                raise LiveTradingNotConfirmed(
                    "Cambiar de broker en live exige CONFIRM_LIVE_TRADING=true"
                )
            changes["broker"] = broker
        if "data_provider" in changes:
            provider = str(changes["data_provider"]).lower()
            if provider not in VALID_DATA_PROVIDERS:
                raise ValueError(f"Proveedor de datos inválido: {provider}")
            changes["data_provider"] = provider
        if "risk_pct" in changes:
            risk = float(changes["risk_pct"])
            if not RISK_PCT_MIN < risk <= RISK_PCT_MAX:
                raise ValueError(f"risk_pct debe estar entre {RISK_PCT_MIN:g} (excluido) y {RISK_PCT_MAX:g}")
            changes["risk_pct"] = risk
        if "strategy_params" in changes:
            if not isinstance(changes["strategy_params"], dict):
                raise ValueError("strategy_params debe ser un objeto")
            unknown_params = set(changes["strategy_params"]) - set(STRATEGY_KEYS)
            if unknown_params:
                raise ValueError(f"strategy_params: claves desconocidas {sorted(unknown_params)}")
            # Base = valores por defecto + actuales (descarta claves antiguas desconocidas).
            current = {k: v for k, v in self.get("strategy_params", {}).items() if k in STRATEGY_KEYS}
            merged = {**DEFAULT_SETTINGS["strategy_params"], **current, **changes["strategy_params"]}
            changes["strategy_params"] = validate_strategy_params(merged)
        if "symbols" in changes:
            changes["symbols"] = normalize_symbols(changes["symbols"])
        if "timeframe" in changes and changes["timeframe"] not in TIMEFRAMES:
            raise ValueError(f"timeframe inválido: {changes['timeframe']} "
                             f"(admitidos: {', '.join(TIMEFRAMES)})")
        for key, lo, hi in (("lookback_days", 1, LOOKBACK_DAYS_MAX),
                            ("loop_interval_sec", LOOP_INTERVAL_MIN, LOOP_INTERVAL_MAX)):
            if key in changes:
                value = changes[key]
                if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                    raise ValueError(f"{key} debe ser un entero entre {lo} y {hi}")
        if "bot_enabled" in changes and not isinstance(changes["bot_enabled"], bool):
            raise ValueError("bot_enabled debe ser true/false")
        unknown = set(changes) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError(f"Claves desconocidas: {sorted(unknown)}")
        with self._lock, self._conn() as conn:
            for key, value in changes.items():
                conn.execute(
                    "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, datetime('now')) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, json.dumps(value)),
                )
        return self.load()

    # ---- optimización y trades -----------------------------------------
    def save_optimization(self, symbol: str, params: dict, metrics: dict, applied: bool) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO optimization_runs(symbol, params, metrics, applied) VALUES (?,?,?,?)",
                (symbol, json.dumps(params), json.dumps(metrics), int(applied)),
            )

    def optimization_history(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM optimization_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {**dict(r), "params": json.loads(r["params"]), "metrics": json.loads(r["metrics"])}
            for r in rows
        ]

    def record_trade(self, symbol: str, side: str, qty: float, price: float | None,
                     mode: str, order_id: str | None = None, pnl: float | None = None,
                     broker: str | None = None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO trades(symbol, side, qty, price, mode, broker, order_id, pnl) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (symbol, side, qty, price, mode, broker, order_id, pnl),
            )

    def trades(self, limit: int = 500) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
