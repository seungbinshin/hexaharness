from __future__ import annotations

from pathlib import Path

from hexaharness.errors import VerificationFailedError
from hexaharness.events import record_event
from hexaharness.io import atomic_write_text
from hexaharness.models import HarnessConfig, TaskState, TaskStatus
from hexaharness.paths import HarnessPaths
from hexaharness.sensors import required_sensors_passed, run_sensors
from hexaharness.state import (
    load_task,
    record_sensor_results,
    save_task,
    set_task_status,
)


def complete_task(
    project_root: Path,
    config: HarnessConfig,
    task_id: str,
    *,
    artifacts: list[str] | None = None,
) -> TaskState:
    state = load_task(project_root, task_id)
    results = run_sensors(project_root, config, task_id=task_id)
    state = record_sensor_results(project_root, task_id, results)
    if not required_sensors_passed(config, results):
        record_event(
            project_root,
            "trip-wire.fired",
            task_id=task_id,
            payload={"name": "sensor-regression"},
        )
        set_task_status(
            project_root,
            task_id,
            TaskStatus.PAUSED,
            error="required computational sensors did not pass",
        )
        raise VerificationFailedError("required computational sensors did not pass")
    for artifact in artifacts or []:
        if artifact not in state.artifacts:
            state.artifacts.append(artifact)
    state.next_step = None
    save_task(project_root, state)
    return set_task_status(project_root, task_id, TaskStatus.COMPLETED)


def stop_task_or_harness(
    project_root: Path,
    *,
    task_id: str | None,
    reason: str,
) -> TaskState | None:
    if task_id:
        return set_task_status(project_root, task_id, TaskStatus.STOPPED, error=reason)
    paths = HarnessPaths(project_root)
    atomic_write_text(paths.stop_file, f"reason: {reason}\n")
    record_event(project_root, "harness.stopped", payload={"reason": reason})
    return None


def resume_task(
    project_root: Path,
    task_id: str,
    *,
    clear_stop: bool = False,
    reactivate: bool = False,
) -> TaskState:
    paths = HarnessPaths(project_root)
    if clear_stop and paths.stop_file.exists():
        paths.stop_file.unlink()
        record_event(project_root, "harness.stop-cleared", task_id=task_id)
    state = load_task(project_root, task_id)
    if reactivate and state.status in {TaskStatus.PAUSED, TaskStatus.ESCALATED}:
        state.status = TaskStatus.ACTIVE
        state.last_error = None
        save_task(project_root, state)
        record_event(project_root, "task.reactivated", task_id=task_id)
    return state
