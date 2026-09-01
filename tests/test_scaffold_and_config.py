from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from hexaharness.config import load_config
from hexaharness.paths import HarnessPaths, find_project_root
from hexaharness.scaffold import initialize_project


def test_initialize_preserves_existing_agent_guide(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("existing guide\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module example.test/sample\n", encoding="utf-8")

    config = initialize_project(tmp_path, project_name="sample")

    assert config.project.language == "Go"
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "existing guide\n"
    assert (tmp_path / "CLAUDE.md").is_file()
    assert HarnessPaths(tmp_path).config.is_file()
    assert any(HarnessPaths(tmp_path).events.glob("*.jsonl"))


def test_initialize_refuses_to_replace_config_without_force(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    with pytest.raises(FileExistsError):
        initialize_project(tmp_path)


def test_find_project_root_from_nested_folder(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == tmp_path


def test_config_rejects_unknown_fields(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    path = HarnessPaths(tmp_path).config
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["unexpected"] = True
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(tmp_path)
