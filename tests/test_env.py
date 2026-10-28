"""Issue #4: el .env se carga antes de leer SETTINGS_DB_PATH / LOG_LEVEL."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from config.settings import SettingsStore
from core import env
from monitoring.logger import log_level

ROOT = Path(__file__).resolve().parents[1]


def test_load_env_respects_existing_env(tmp_path, monkeypatch):
    f = tmp_path / "custom.env"
    f.write_text("TB_TEST_A=desde_env\nTB_TEST_B=desde_env\n")
    monkeypatch.setenv("ENV_FILE", str(f))
    monkeypatch.delenv("SKIP_DOTENV", raising=False)
    monkeypatch.delenv("TB_TEST_A", raising=False)
    monkeypatch.setenv("TB_TEST_B", "del_shell")
    try:
        assert env.load_env(force=True) == [f]
        assert os.environ["TB_TEST_A"] == "desde_env"
        assert os.environ["TB_TEST_B"] == "del_shell"   # el entorno real tiene prioridad
    finally:
        os.environ.pop("TB_TEST_A", None)


def test_settings_db_path_resolved_at_runtime(tmp_path, monkeypatch):
    db = tmp_path / "otra" / "settings.db"
    monkeypatch.setenv("SETTINGS_DB_PATH", str(db))
    assert SettingsStore().db_path == db and db.exists()


def test_invalid_log_level_falls_back_to_info(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    assert log_level() == logging.INFO
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert log_level() == logging.DEBUG


def test_dotenv_applies_without_docker(tmp_path):
    """Proceso nuevo, sin variables exportadas: el .env del cwd fija la BD."""
    db = tmp_path / "desde_dotenv.db"
    (tmp_path / ".env").write_text(f"SETTINGS_DB_PATH={db}\nLOG_LEVEL=WARNING\n")
    clean = {k: v for k, v in os.environ.items()
             if k not in {"SETTINGS_DB_PATH", "LOG_LEVEL", "SKIP_DOTENV", "ENV_FILE"}}
    clean["PYTHONPATH"] = str(ROOT)
    code = ("from config.settings import SettingsStore; from monitoring.logger import get_logger;"
            "print(SettingsStore().db_path); print(get_logger('x').level)")
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=clean,
                         capture_output=True, text=True, check=True).stdout.split()
    assert out == [str(db), str(logging.WARNING)]
