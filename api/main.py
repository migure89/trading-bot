"""Backend FastAPI de control y telemetría del bot (Alpaca / Interactive Brokers)."""
from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from config.settings import (
    LOOKBACK_DAYS_MAX,
    LOOP_INTERVAL_MAX,
    LOOP_INTERVAL_MIN,
    RISK_PCT_MAX,
    LiveTradingNotConfirmed,
    SettingsStore,
    normalize_symbols,
    resolve_data_provider,
)
from execution.base import BaseBroker, BrokerConnectionError
from execution.factory import BrokerRegistry
from monitoring.logger import setup_logging

# JSON también para uvicorn (access/error): se importa después de que uvicorn configure su logging.
setup_logging(force=True)
log = logging.getLogger("api")

# Rutas públicas (sin token): healthcheck de Docker/balanceadores.
PUBLIC_PATHS = frozenset({"/health"})

_bearer = HTTPBearer(auto_error=False, description="Token de API_TOKEN")
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False, description="Token de API_TOKEN")


def api_token() -> str:
    """Token configurado en ``API_TOKEN`` (vacío = autenticación desactivada)."""
    return os.getenv("API_TOKEN", "").strip()


def require_token(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Security(_bearer),
    api_key: str | None = Security(_api_key),
) -> None:
    """Exige ``Authorization: Bearer <API_TOKEN>`` o ``X-API-Key: <API_TOKEN>`` si hay token."""
    expected = api_token()
    if not expected or request.url.path in PUBLIC_PATHS:
        return
    supplied = (bearer.credentials if bearer else None) or api_key or ""
    if not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Token de API ausente o no válido",
                            headers={"WWW-Authenticate": "Bearer"})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not api_token():
        log.warning("API_TOKEN no está definido: la API de control NO tiene autenticación. "
                    "Defínelo en .env y no expongas el puerto fuera de localhost.")
    yield


app = FastAPI(title="Trading Bot API", version="1.3.0", lifespan=lifespan,
              dependencies=[Depends(require_token)])


def mask_account(account: str | None) -> str | None:
    """'DU1234567' -> 'DU****567': suficiente para distinguir cuentas sin exponer el número."""
    if not account:
        return account
    if len(account) <= 5:
        return "*" * len(account)
    return f"{account[:2]}{'*' * (len(account) - 5)}{account[-3:]}"


def public_connection(info: dict) -> dict:
    """Copia del estado de conexión con los números de cuenta enmascarados."""
    info = dict(info)
    if "account" in info:
        info["account"] = mask_account(info["account"])
    if "accounts" in info:
        info["accounts"] = [mask_account(a) for a in info["accounts"] or []]
    return info

_store: SettingsStore | None = None
_brokers: BrokerRegistry | None = None


def get_store() -> SettingsStore:
    global _store
    if _store is None:
        _store = SettingsStore()
    return _store


def get_brokers() -> BrokerRegistry:
    global _brokers
    if _brokers is None:
        _brokers = BrokerRegistry()
    return _brokers


def active_broker(store: SettingsStore, brokers: BrokerRegistry) -> BaseBroker:
    """Broker activo según la configuración, ya preparado para el modo actual."""
    cfg = store.load()
    broker = brokers.get(cfg["broker"])
    try:
        broker.ensure_mode(cfg["mode"])
    except LiveTradingNotConfirmed as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except BrokerConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return broker


def broker_call(fn, *args, action: str = "la operación"):
    """Ejecuta una operación del broker y traduce sus errores a respuestas HTTP claras.

    - ``BrokerConnectionError`` (sin credenciales, TWS/Gateway caído): 503.
    - Cualquier otro error del broker (orden rechazada, 401 de Alpaca…): 502 con el motivo,
      en lugar de un 500 con traceback.
    """
    try:
        return fn(*args)
    except HTTPException:
        raise
    except BrokerConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"El broker rechazó {action}: {exc}") from exc


class StrictModel(BaseModel):
    """Las claves desconocidas dan 422 en lugar de ignorarse en silencio."""

    model_config = ConfigDict(extra="forbid")


class StrategyParamsUpdate(StrictModel):
    """Cambios parciales de strategy_params (mismos límites que ``StrategyParams``).

    Las reglas cruzadas (ema_fast < ema_slow, rsi_oversold < rsi_overbought) se validan en
    el store tras fusionar con los valores actuales.
    """

    ema_fast: int | None = Field(None, ge=1, le=500)
    ema_slow: int | None = Field(None, ge=2, le=1000)
    rsi_period: int | None = Field(None, ge=2, le=200)
    rsi_overbought: float | None = Field(None, gt=0, lt=100)
    rsi_oversold: float | None = Field(None, gt=0, lt=100)
    atr_period: int | None = Field(None, ge=1, le=200)
    atr_stop_mult: float | None = Field(None, gt=0, le=20)
    bb_period: int | None = Field(None, ge=2, le=500)
    bb_std: float | None = Field(None, gt=0, le=10)


class ConfigUpdate(StrictModel):
    mode: Literal["paper", "live"] | None = None
    broker: Literal["alpaca", "ibkr"] | None = None
    data_provider: Literal["auto", "alpaca", "ibkr"] | None = None
    symbols: list[str] | None = Field(None, min_length=1, max_length=100)
    risk_pct: float | None = Field(None, gt=0, le=RISK_PCT_MAX)
    timeframe: Literal["1Min", "5Min", "15Min", "1Hour", "1Day"] | None = None
    loop_interval_sec: int | None = Field(None, ge=LOOP_INTERVAL_MIN, le=LOOP_INTERVAL_MAX)
    lookback_days: int | None = Field(None, ge=1, le=LOOKBACK_DAYS_MAX)
    regular_hours_only: StrictBool | None = None
    strategy_params: StrategyParamsUpdate | None = None
    daily_run_time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shadow_strategy: Literal["off", "blend", "trend_sma", "rsi2"] | None = None
    shadow_symbols: list[str] | None = Field(None, min_length=1, max_length=100)
    # Confirmación explícita para cambiar de broker estando (o pasando) a live.
    confirm_live: bool = False

    @field_validator("symbols", "shadow_symbols")
    @classmethod
    def _symbols(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else normalize_symbols(v)


class ToggleRequest(StrictModel):
    enabled: bool | None = None          # None = alternar
    close_positions: bool = False        # kill switch total: liquida todo


class ClosePositionRequest(StrictModel):
    symbol: str


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/status")
def status(store: SettingsStore = Depends(get_store), brokers: BrokerRegistry = Depends(get_brokers)):
    cfg = store.load()
    out: dict[str, Any] = {"mode": cfg["mode"], "bot_enabled": cfg["bot_enabled"],
                           "broker": cfg["broker"], "data_provider": resolve_data_provider(cfg),
                           "symbols": cfg["symbols"], "account": None, "positions": []}
    broker = brokers.get(cfg["broker"])
    try:
        broker.ensure_mode(cfg["mode"])
        out["account"] = broker.account()
        out["positions"] = broker.positions()
        out["unrealized_pl"] = sum(p["unrealized_pl"] for p in out["positions"])
    except Exception as exc:  # sin credenciales o broker caído: la API sigue respondiendo
        out["error"] = str(exc)
    try:
        out["connection"] = public_connection(broker.connection_status())
    except Exception as exc:
        out["connection"] = {"broker": cfg["broker"], "connected": False, "last_error": str(exc)}
    return out


@app.get("/brokers")
def list_brokers(store: SettingsStore = Depends(get_store), brokers: BrokerRegistry = Depends(get_brokers)):
    cfg = store.load()
    return {"active": cfg["broker"], "data_provider": resolve_data_provider(cfg),
            "mode": cfg["mode"], "available": brokers.available(),
            "connections": {k: public_connection(v) for k, v in brokers.statuses().items()}}


@app.get("/config")
def read_config(store: SettingsStore = Depends(get_store)):
    return store.load()


@app.post("/config")
def update_config(body: ConfigUpdate, store: SettingsStore = Depends(get_store)):
    changes = body.model_dump(exclude_none=True)
    confirm_live = changes.pop("confirm_live", False)
    current = store.load()
    target_mode = changes.get("mode", current["mode"])
    if ("broker" in changes and changes["broker"] != current["broker"]
            and target_mode == "live" and not confirm_live):
        raise HTTPException(
            status_code=409,
            detail="Cambiar de broker en modo LIVE requiere confirm_live=true (operarás con dinero real)",
        )
    try:
        return store.update(changes)
    except LiveTradingNotConfirmed as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/toggle-bot")
def toggle_bot(body: ToggleRequest, store: SettingsStore = Depends(get_store),
               brokers: BrokerRegistry = Depends(get_brokers)):
    enabled = (not store.get("bot_enabled")) if body.enabled is None else body.enabled
    store.update({"bot_enabled": enabled})  # se pausa antes de tocar el broker
    closed = False
    if body.close_positions:
        try:
            broker = active_broker(store, brokers)
            broker_call(broker.close_all, action="el cierre de posiciones")
        except HTTPException as exc:
            estado = "pausado" if not enabled else "activo"
            raise HTTPException(
                status_code=exc.status_code,
                detail=f"El bot quedó {estado}, pero NO se pudieron cerrar las posiciones: {exc.detail}",
            ) from exc
        closed = True
    return {"bot_enabled": enabled, "positions_closed": closed}


@app.post("/cancel-orders")
def cancel_orders(store: SettingsStore = Depends(get_store), brokers: BrokerRegistry = Depends(get_brokers)):
    broker = active_broker(store, brokers)
    broker_call(broker.cancel_all_orders, action="la cancelación de órdenes")
    return {"cancelled": True, "broker": broker.name}


@app.post("/close-position")
def close_position(body: ClosePositionRequest, store: SettingsStore = Depends(get_store),
                   brokers: BrokerRegistry = Depends(get_brokers)):
    broker = active_broker(store, brokers)
    broker_call(broker.close_position, body.symbol, action=f"el cierre de {body.symbol}")
    return {"closed": body.symbol}


@app.get("/shadow")
def shadow(limit: int = 30, store: SettingsStore = Depends(get_store)):
    """Señales de la estrategia en sombra (solo lectura; la sombra nunca envía órdenes)."""
    history = store.shadow_history(max(1, min(int(limit), 365)))
    cfg = store.load()
    return {"strategy": cfg.get("shadow_strategy", "off"), "symbols": cfg.get("shadow_symbols", []),
            "daily_run_time": cfg.get("daily_run_time"), "orders": False,
            "latest": history[0] if history else None, "history": history}


@app.get("/metrics")
def metrics(store: SettingsStore = Depends(get_store)):
    trades = store.trades()
    pnls = [t["pnl"] for t in trades if t["pnl"] is not None]
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    return {
        "num_trades": len(trades),
        "realized_pnl": sum(pnls),
        "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "profit_factor": (sum(wins) / sum(losses)) if losses else None,
        "trades": trades,
        "optimization_history": store.optimization_history(),
        "strategy_params": store.get("strategy_params"),
    }
