from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hexaharness.config import load_config, save_config
from hexaharness.models import CommandSpec, PolicyConfig, ProjectConfig
from hexaharness.scaffold import initialize_project


@pytest.fixture
def harness_project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    initialize_project(tmp_path, project_name="sample")
    config = load_config(tmp_path)
    passing = [sys.executable, "-c", "print('sensor ok')"]
    config.project = ProjectConfig(
        name="sample",
        language="Python",
        build=passing,
        test=passing,
        lint=passing,
        typecheck=passing,
    )
    config.sensors = [CommandSpec(name="test", argv=passing, timeout_seconds=10, required=True)]
    config.policy = PolicyConfig(
        allow_execute=[[sys.executable]],
        ask_execute=[["git", "push"]],
        deny_execute=[["rm", "-rf"]],
        write_paths=["src/**", ".hexaharness/**"],
        ask_write_paths=["**/*"],
    )
    save_config(tmp_path, config)
    return tmp_path
