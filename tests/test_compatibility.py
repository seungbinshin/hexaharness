"""Golden wire contracts produced by the unmodified 0.4.0 base commit."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from hexaharness.cli import app
from hexaharness.config import load_config, save_config
from hexaharness.models import HarnessConfig
from hexaharness.paths import HarnessPaths
from hexaharness.state import configuration_fingerprint, load_task

FIXTURE = Path(__file__).parent / "fixtures/legacy-040-contract.json"


def test_legacy_config_keeps_wire_shape_and_sensor_fingerprint(tmp_path: Path) -> None:
    golden = json.loads(FIXTURE.read_text())
    config = HarnessConfig.model_validate(golden["config"])
    assert config.improvement is None
    assert configuration_fingerprint(config) == golden["config_sha256"]
    save_config(tmp_path, config)
    assert yaml.safe_load(HarnessPaths(tmp_path).config.read_text()) == golden["config"]
    assert load_config(tmp_path).model_dump(mode="json") == golden["config"]


def test_legacy_checkpoint_status_and_resume_keep_wire_shape(tmp_path: Path) -> None:
    golden = json.loads(FIXTURE.read_text())
    config = HarnessConfig.model_validate(golden["config"])
    save_config(tmp_path, config)
    path = HarnessPaths(tmp_path).state_file("legacy-task")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(golden["task"]))
    before = path.read_bytes()
    assert load_task(tmp_path, "legacy-task").model_dump(mode="json") == golden["task"]
    runner = CliRunner()
    status = runner.invoke(app, ["status", "--root", str(tmp_path)])
    assert status.exit_code == 0, status.output
    assert json.loads(status.stdout) == [golden["task"]]
    resumed = runner.invoke(app, ["resume", "legacy-task", "--root", str(tmp_path)])
    assert resumed.exit_code == 0, resumed.output
    assert json.loads(resumed.stdout) == golden["task"]
    assert path.read_bytes() == before
