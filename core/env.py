"""Carga del fichero ``.env`` antes de leer cualquier variable de entorno.

Se importa desde ``config.settings`` y ``monitoring.logger``, que son los primeros módulos
en leer el entorno, así que basta con importar cualquier parte de la plataforma para que
``SETTINGS_DB_PATH``, ``LOG_LEVEL``, ``LOG_FILE``… del ``.env`` se respeten también sin Docker.

- Las variables ya definidas en el entorno (Docker ``env_file``, shell, CI) tienen prioridad.
- ``ENV_FILE`` permite indicar otro fichero; si no, se busca ``.env`` en el directorio actual
  y en la raíz del proyecto.
- ``SKIP_DOTENV=1`` desactiva la carga (lo usan los tests para no depender del ``.env`` local).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_loaded = False


def candidate_files() -> list[Path]:
    if os.getenv("ENV_FILE"):
        return [Path(os.environ["ENV_FILE"])]
    files = [Path.cwd() / ".env", PROJECT_ROOT / ".env"]
    return list(dict.fromkeys(f.resolve() for f in files))


def load_env(force: bool = False) -> list[Path]:
    """Carga los ``.env`` encontrados (una sola vez por proceso) y devuelve los usados."""
    global _loaded
    if (_loaded and not force) or os.getenv("SKIP_DOTENV", "").lower() in {"1", "true", "yes"}:
        return []
    used = [f for f in candidate_files() if f.is_file()]
    for f in used:
        load_dotenv(f, override=False)
    _loaded = True
    return used


load_env()
