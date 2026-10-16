"""Backend FastAPI de control y telemetría del bot."""
from __future__ import annotations

from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from config.settings import LiveTradingNotConfirmed, SettingsStore
from execution.broker import AlpacaBroker

load_dotenv()
app = FastAPI(title="Trading Bot API", version="1.0.0")

_store: SettingsStore | None = None
_broker: AlpacaBroker | None = None


def get_store() -> SettingsStore:
    global _store
    if _store is None:
        _store = SettingsStore()
    return _store


def get_broker() -> AlpacaBroker:
    global _broker
    if _broker is None:
        _broker = AlpacaBroker()
    return _broker


class ConfigUpdate(BaseModel):
    mode: str | None = Field(None, pattern="^(paper|live)$")
    symbols: list[str] | None = None
    risk_pct: float | None = Field(None, gt=0, le=10)
    timeframe: str | None = None
    loop_interval_sec: int | None = Field(None, ge=5)
    lookback_days: int | None = Field(None, ge=1)
    strategy_params: dict[str, Any] | None = None


class ToggleRequest(BaseModel):
    enabled: bool | None = None          # None = alternar
    close_positions: bool = False        # kill switch total: liquida todo


class ClosePositionRequest(BaseModel):
    symbol: str


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/status")
def status(store: SettingsStore = Depends(get_store), broker: AlpacaBroker = Depends(get_broker)):
    cfg = store.load()
    out: dict[str, Any] = {"mode": cfg["mode"], "bot_enabled": cfg["bot_enabled"],
                           "symbols": cfg["symbols"], "account": None, "positions": []}
    try:
        broker.ensure_mode(cfg["mode"])
        out["account"] = broker.account()
        out["positions"] = broker.positions()
        out["unrealized_pl"] = sum(p["unrealized_pl"] for p in out["positions"])
    except Exception as exc:  # sin credenciales o Alpaca caído: la API sigue respondiendo
        out["error"] = str(exc)
    return out


@app.get("/config")
def read_config(store: SettingsStore = Depends(get_store)):
    return store.load()


@app.post("/config")
def update_config(body: ConfigUpdate, store: SettingsStore = Depends(get_store)):
    changes = body.model_dump(exclude_none=True)
    try:
        return store.update(changes)
    except LiveTradingNotConfirmed as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/toggle-bot")
def toggle_bot(body: ToggleRequest, store: SettingsStore = Depends(get_store),
               broker: AlpacaBroker = Depends(get_broker)):
    enabled = (not store.get("bot_enabled")) if body.enabled is None else body.enabled
    store.update({"bot_enabled": enabled})
    closed = False
    if body.close_positions:
        broker.ensure_mode(store.get("mode"))
        broker.close_all()
        closed = True
    return {"bot_enabled": enabled, "positions_closed": closed}


@app.post("/close-position")
def close_position(body: ClosePositionRequest, store: SettingsStore = Depends(get_store),
                   broker: AlpacaBroker = Depends(get_broker)):
    broker.ensure_mode(store.get("mode"))
    broker.close_position(body.symbol)
    return {"closed": body.symbol}


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
