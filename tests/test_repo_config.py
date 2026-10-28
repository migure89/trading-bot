"""Comprobaciones estáticas del repositorio (workflows, dependencias, dashboard)."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_workflows_are_valid_yaml():
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        assert yaml.safe_load(wf.read_text())["jobs"], wf.name


def test_scheduled_optimizer_writes_and_validates_json():   # issue #11
    wf = yaml.safe_load((ROOT / ".github/workflows/backtest_scheduled.yml").read_text())
    script = "\n".join(s.get("run", "") for s in wf["jobs"]["optimize"]["steps"])
    assert "--output artifacts/optimization.json" in script
    assert "| tee artifacts/optimization.json" not in script
    assert "jq -e" in script


def test_requirements_have_upper_bounds():                   # issue #15
    for name in ("requirements.txt", "requirements-dev.txt"):
        for line in (ROOT / name).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith("-r"):
                assert "<" in line, f"{name}: {line} sin versión máxima"


def test_dashboard_has_no_deprecated_width():                # issue #15
    assert not re.search(r"use_container_width", (ROOT / "dashboard/app.py").read_text())
