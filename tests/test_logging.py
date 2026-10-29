"""Issue #8: todas las líneas de log de todos los procesos son JSON válido."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_py(code: str, **env) -> subprocess.CompletedProcess:
    full_env = {**os.environ, "PYTHONPATH": str(ROOT), "SKIP_DOTENV": "1", **env}
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, env=full_env,
                          capture_output=True, text=True, timeout=60)


def json_lines(text: str) -> list[dict]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines, "no se emitió ningún log"
    return [json.loads(ln) for ln in lines]   # falla si alguna línea no es JSON


def test_third_party_and_tracebacks_are_json(tmp_path):
    log_file = tmp_path / "app.log"
    proc = run_py("""
        import logging, warnings
        from monitoring.logger import get_logger
        log = get_logger("bot")
        log.info("hola", extra={"symbol": "AAPL"})
        logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d',
                                                 "127.0.0.1:1", "GET", "/health", "1.1", 200)
        logging.getLogger("uvicorn.error").error("Exception in ASGI application")
        logging.getLogger("ib_async.wrapper").error("Error 201, reqId 3: Order rejected")
        logging.getLogger("ib_async.client").error("API connection failed: ConnectionRefusedError")
        warnings.warn("aviso de prueba")
        try:
            1 / 0
        except ZeroDivisionError:
            log.exception("fallo")
    """, LOG_FILE=str(log_file))
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    records = json_lines(proc.stdout)
    assert json_lines(log_file.read_text()) == records
    by_logger = {r["logger"]: r for r in records}
    assert by_logger["uvicorn.access"]["msg"].endswith('"GET /health HTTP/1.1" 200')
    assert "ib_async.client" not in by_logger            # duplicado del ERROR del bot (#7)
    assert by_logger["ib_async.wrapper"]["level"] == "ERROR"
    assert "ZeroDivisionError" in by_logger["bot"]["exc"]


def test_uncaught_exception_is_json():
    proc = run_py("""
        from monitoring.logger import install_excepthook, setup_logging
        import sys
        setup_logging(stream=sys.stderr, force=True)
        install_excepthook("optimizer")
        raise RuntimeError("sin datos")
    """)
    assert proc.returncode != 0 and proc.stdout == ""
    (rec,) = json_lines(proc.stderr)
    assert rec["level"] == "CRITICAL" and "RuntimeError: sin datos" in rec["exc"]


def test_uvicorn_logs_are_json_after_api_import():
    """Simula el arranque de uvicorn (que configura sus loggers de texto) y luego importa la API."""
    proc = run_py("""
        import logging.config
        from uvicorn.config import LOGGING_CONFIG
        logging.config.dictConfig(LOGGING_CONFIG)
        import api.main  # noqa: F401
        logging.getLogger("uvicorn.error").info("Started server process")
        logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d',
                                                 "127.0.0.1:1", "GET", "/status", "1.1", 200)
    """)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    msgs = [r["msg"] for r in json_lines(proc.stdout)]
    assert "Started server process" in msgs
