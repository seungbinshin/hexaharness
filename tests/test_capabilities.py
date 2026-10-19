from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hexaharness import __version__
from hexaharness.cli import app


@pytest.mark.parametrize("invalid_project", [False, True])
def test_discovery_is_read_only_and_does_not_require_a_valid_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_project: bool
) -> None:
    if invalid_project:
        config = tmp_path / ".hexaharness/harness.yaml"
        config.parent.mkdir()
        config.write_text("schema_version: 999\ninvalid: [", encoding="utf-8")
    before = {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["capabilities"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["runtime_version"] == __version__
    assert payload["contracts"]["config_read"] == [1, 2]
    assert payload["contracts"]["host_handoff"] == 1
    assert payload["capabilities"]["host_handoff"] is True
    assert payload["capabilities"]["provider_dispatch"] is False
    assert payload["capabilities"]["native_os_sandbox"] is False
    assert payload["capabilities"]["code_optimization"] is False
    assert payload["host_authority_required"] is True
    assert payload["supported_evaluators"] == ["literal-text-v1"]
    assert payload["provider_evidence"] == "not-assessed"
    assert str(tmp_path) not in result.stdout
    after = {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
    assert after == before
    if not invalid_project:
        assert list(tmp_path.iterdir()) == []
