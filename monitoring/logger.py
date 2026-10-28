"""Logging estructurado en JSON (una línea por evento) para todos los procesos.

``setup_logging()`` configura el *root logger* con ``JsonFormatter`` y redirige a él los loggers de
terceros (uvicorn, ib_async, optuna…), de modo que cada línea de log es un JSON válido y los
tracebacks van dentro del campo ``exc``.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import TextIO

from core.env import load_env

load_env()  # LOG_LEVEL y LOG_FILE pueden venir del .env

_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message", "asctime", "color_message", "taskName"}

# Loggers de terceros que se redirigen al root (sin handlers propios).
THIRD_PARTY = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi",
               "ib_async", "optuna", "streamlit")
# ib_async.client repite como ERROR cada fallo de conexión que el bot ya registra (con backoff).
DEFAULT_LEVELS = {"ib_async.client": "CRITICAL"}

_HANDLER_FLAG = "_trading_bot_json"
_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def log_level(value: str | None = None) -> int:
    """Nivel de LOG_LEVEL (nombre o número); INFO si no es válido."""
    value = (value if value is not None else os.getenv("LOG_LEVEL", "INFO")).strip().upper()
    level = int(value) if value.isdigit() else logging.getLevelName(value)
    return level if isinstance(level, int) else logging.INFO


def _third_party_levels() -> dict[str, str]:
    """Niveles por logger; se pueden cambiar con ``LOG_LEVELS=ib_async.client=INFO,optuna=DEBUG``."""
    levels = dict(DEFAULT_LEVELS)
    for item in os.getenv("LOG_LEVELS", "").split(","):
        if "=" in item:
            name, lvl = item.split("=", 1)
            levels[name.strip()] = lvl.strip()
    return levels


def setup_logging(stream: TextIO | None = None, force: bool = False) -> logging.Logger:
    """Configura el root logger en JSON (idempotente; ``force`` para reconfigurar, p.ej. a stderr)."""
    global _configured
    root = logging.getLogger()
    if _configured and not force:
        return root
    for h in list(root.handlers):
        # Quita los nuestros y los de texto de basicConfig; respeta otros (p.ej. caplog de pytest).
        if getattr(h, _HANDLER_FLAG, False) or type(h) is logging.StreamHandler:
            root.removeHandler(h)
            if isinstance(h, logging.FileHandler):
                h.close()
    formatter = JsonFormatter()
    handlers: list[logging.Handler] = [logging.StreamHandler(stream or sys.stdout)]
    if os.getenv("LOG_FILE"):
        handlers.append(logging.FileHandler(os.environ["LOG_FILE"], encoding="utf-8"))
    for h in handlers:
        h.setFormatter(formatter)
        setattr(h, _HANDLER_FLAG, True)
        root.addHandler(h)
    root.setLevel(log_level())

    for name in THIRD_PARTY:
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    for name, lvl in _third_party_levels().items():
        logging.getLogger(name).setLevel(log_level(lvl))
    if "optuna" in sys.modules:   # optuna instala su propio handler de texto
        import optuna

        optuna.logging.disable_default_handler()
        optuna.logging.enable_propagation()
    logging.captureWarnings(True)
    _configured = True
    return root


def install_excepthook(logger_name: str = "app") -> None:
    """Las excepciones no capturadas se registran en JSON (campo ``exc``) en lugar de un traceback."""
    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logging.getLogger(logger_name).critical("excepción no capturada", exc_info=(exc_type, exc, tb))
    sys.excepthook = hook


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
