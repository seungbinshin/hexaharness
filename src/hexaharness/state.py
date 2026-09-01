from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

from hexaharness.errors import TaskNotFoundError
from hexaharness.events import record_event
from hexaharness.io import read_json, slugify, write_json
from hexaharness.models import SensorResult, TaskState, TaskStatus, utc_now
from hexaharness.paths import HarnessPaths

TASK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,99}$")


def validate_task_id(task_id: str) -> str:
    if not TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError("task IDs must contain 3-100 lowercase letters, digits, or hyphens")
    return task_id


def make_task_id(goal: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp.lower()}-{slugify(goal, limit=28)}-{secrets.token_hex(2)}"


def save_task(project_root: Path, state: TaskState) -> None:
    validate_task_id(state.task_id)
    state.updated_at = utc_now()
    paths = HarnessPaths(project_root)
    paths.states.mkdir(parents=True, exist_ok=True)
    write_json(paths.state_file(state.task_id), state.model_dump(mode="json"))


def load_task(project_root: Path, task_id: str) -> TaskState:
    validate_task_id(task_id)
    path = HarnessPaths(project_root).state_file(task_id)
    if not path.is_file():
        raise TaskNotFoundError(f"task checkpoint not found: {task_id}")
    return TaskState.model_validate(read_json(path))


def list_tasks(project_root: Path) -> list[TaskState]:
    states = HarnessPaths(project_root).states
    if not states.is_dir():
        return []
    tasks = [TaskState.model_validate(read_json(path)) for path in states.glob("*.json")]
    return sorted(tasks, key=lambda task: task.updated_at, reverse=True)


def start_task(project_root: Path, goal: str, *, unattended: bool = False) -> TaskState:
    state = TaskState(task_id=make_task_id(goal), goal=goal, unattended=unattended)
    save_task(project_root, state)
    record_event(
        project_root,
        "task.started",
        task_id=state.task_id,
        payload={"goal": goal, "unattended": unattended},
    )
    return state


def checkpoint_task(
    project_root: Path,
    task_id: str,
    *,
    completed_step: str | None = None,
    next_step: str | None = None,
    artifacts: list[str] | None = None,
    tokens: int = 0,
    cost_usd: float = 0.0,
) -> TaskState:
    state = load_task(project_root, task_id)
    if completed_step and completed_step not in state.completed_steps:
        state.completed_steps.append(completed_step)
    if next_step is not None:
        state.next_step = next_step
    for artifact in artifacts or []:
        if artifact not in state.artifacts:
            state.artifacts.append(artifact)
    state.tokens_used += tokens
    state.cost_usd += cost_usd
    save_task(project_root, state)
    record_event(
        project_root,
        "task.checkpointed",
        task_id=task_id,
        payload={
            "completed_step": completed_step,
            "next_step": next_step,
            "artifact_count": len(artifacts or []),
            "tokens_added": tokens,
            "cost_added_usd": cost_usd,
        },
    )
    return state


def record_sensor_results(
    project_root: Path, task_id: str, results: list[SensorResult]
) -> TaskState:
    state = load_task(project_root, task_id)
    state.sensor_results = results
    save_task(project_root, state)
    return state


def set_task_status(
    project_root: Path,
    task_id: str,
    status: TaskStatus,
    *,
    error: str | None = None,
) -> TaskState:
    state = load_task(project_root, task_id)
    state.status = status
    state.last_error = error
    if status in {TaskStatus.COMPLETED, TaskStatus.STOPPED, TaskStatus.FAILED}:
        state.ended_at = utc_now()
    save_task(project_root, state)
    record_event(
        project_root,
        f"task.{status.value}",
        task_id=task_id,
        payload={"error": error} if error else {},
    )
    return state
