from __future__ import annotations

from pathlib import Path

import pytest

from hexaharness.config import load_config, save_config
from hexaharness.errors import VerificationFailedError
from hexaharness.models import CommandSpec, TaskStatus
from hexaharness.paths import HarnessPaths
from hexaharness.state import checkpoint_task, load_task, start_task
from hexaharness.workflow import complete_task, resume_task, stop_task_or_harness


def test_checkpoint_survives_reload(harness_project: Path) -> None:
    task = start_task(harness_project, "Build export")
    checkpoint_task(
        harness_project,
        task.task_id,
        completed_step="mapped fields",
        next_step="write exporter",
        artifacts=["docs/map.md"],
        tokens=42,
        cost_usd=0.25,
    )

    recovered = load_task(harness_project, task.task_id)
    assert recovered.completed_steps == ["mapped fields"]
    assert recovered.next_step == "write exporter"
    assert recovered.artifacts == ["docs/map.md"]
    assert recovered.tokens_used == 42
    assert recovered.cost_usd == pytest.approx(0.25)


def test_complete_requires_passing_sensors(harness_project: Path) -> None:
    task = start_task(harness_project, "Verified task", unattended=True)
    completed = complete_task(
        harness_project, load_config(harness_project), task.task_id, artifacts=["result.txt"]
    )
    assert completed.status == TaskStatus.COMPLETED
    assert completed.sensor_results[0].passed
    assert completed.artifacts == ["result.txt"]


def test_failed_sensor_pauses_task(harness_project: Path) -> None:
    config = load_config(harness_project)
    config.sensors = [
        CommandSpec(
            name="failure",
            argv=[config.project.test[0], "-c", "raise SystemExit(9)"],
            timeout_seconds=10,
        )
    ]
    save_config(harness_project, config)
    task = start_task(harness_project, "Failing task")

    with pytest.raises(VerificationFailedError):
        complete_task(harness_project, config, task.task_id)

    assert load_task(harness_project, task.task_id).status == TaskStatus.PAUSED


def test_stop_preserves_state_and_resume_clears_global_stop(harness_project: Path) -> None:
    task = start_task(harness_project, "Recoverable task")
    stopped = stop_task_or_harness(harness_project, task_id=task.task_id, reason="operator review")
    assert stopped is not None and stopped.status == TaskStatus.STOPPED

    stop_task_or_harness(harness_project, task_id=None, reason="incident")
    assert HarnessPaths(harness_project).stop_file.is_file()
    resumed = resume_task(harness_project, task.task_id, clear_stop=True)
    assert resumed.status == TaskStatus.STOPPED
    assert not HarnessPaths(harness_project).stop_file.exists()
