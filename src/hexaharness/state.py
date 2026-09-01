from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from hexaharness.errors import PolicyBlockedError, TaskNotFoundError
from hexaharness.events import iter_events, record_event
from hexaharness.io import read_json, slugify, write_json
from hexaharness.models import (
    ArtifactEvidence,
    HarnessConfig,
    SensorResult,
    TaskState,
    TaskStatus,
    WriteObservation,
    utc_now,
)
from hexaharness.paths import HarnessPaths

TASK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,99}$")

PENDING_EXTERNAL_ACTION = "PENDING_EXTERNAL_ACTION:"
RECONCILE_EXTERNAL_ACTION = "RECONCILE_EXTERNAL_ACTION:"
VERIFY_EXTERNAL_ACTION_RESULT = "VERIFY_EXTERNAL_ACTION_RESULT:"
EXTERNAL_ACTION_MARKERS = (
    PENDING_EXTERNAL_ACTION,
    RECONCILE_EXTERNAL_ACTION,
    VERIFY_EXTERNAL_ACTION_RESULT,
)
EXTERNAL_ACTION_BINDING_PATTERN = re.compile(
    r"\[action-nonce:(?P<nonce>[0-9a-f]{32});argv-hmac-sha256:[0-9a-f]{64}\]$"
)
GUIDANCE_SNAPSHOT_PATHS = (
    ".hexaharness/harness.yaml",
    ".hexaharness/GUIDES.md",
    "AGENTS.md",
    "CLAUDE.md",
)

TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.STOPPED, TaskStatus.FAILED})
ALLOWED_STATUS_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.ACTIVE: frozenset(
        {
            TaskStatus.PAUSED,
            TaskStatus.STOPPED,
            TaskStatus.COMPLETED,
            TaskStatus.ESCALATED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.PAUSED: frozenset(
        {TaskStatus.ACTIVE, TaskStatus.STOPPED, TaskStatus.ESCALATED, TaskStatus.FAILED}
    ),
    TaskStatus.ESCALATED: frozenset({TaskStatus.ACTIVE, TaskStatus.STOPPED, TaskStatus.FAILED}),
    TaskStatus.STOPPED: frozenset(),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
}

_PROCESS_LOCKS: dict[str, Any] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()
_HELD_LOCKS = threading.local()


def _reset_locks_after_fork() -> None:  # pragma: no cover - fork timing is nondeterministic
    global _PROCESS_LOCKS, _PROCESS_LOCKS_GUARD, _HELD_LOCKS
    _PROCESS_LOCKS = {}
    _PROCESS_LOCKS_GUARD = threading.Lock()
    _HELD_LOCKS = threading.local()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_locks_after_fork)


def validate_task_id(task_id: str) -> str:
    if not TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError("task IDs must contain 3-100 lowercase letters, digits, or hyphens")
    return task_id


def _process_lock(key: str) -> Any:
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.RLock())


def _thread_held_locks() -> set[str]:
    held = getattr(_HELD_LOCKS, "keys", None)
    if held is None:
        held = set()
        _HELD_LOCKS.keys = held
    return cast(set[str], held)


def _acquire_file_lock(descriptor: int) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        msvcrt = cast(Any, import_module("msvcrt"))

        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX)


def _release_file_lock(descriptor: int) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        msvcrt = cast(Any, import_module("msvcrt"))

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def _named_lock(project_root: Path, filename: str) -> Iterator[None]:
    root = project_root.resolve()
    lock_path = HarnessPaths(root).states / filename
    key = str(lock_path)
    local_lock = _process_lock(key)
    with local_lock:
        held = _thread_held_locks()
        if key in held:
            yield
            return

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            _acquire_file_lock(descriptor)
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                _release_file_lock(descriptor)
        finally:
            os.close(descriptor)


@contextmanager
def task_lock(project_root: Path, task_id: str) -> Iterator[None]:
    """Serialize a task's read-modify-write operations across threads and processes."""
    validate_task_id(task_id)
    with _named_lock(project_root, f"{task_id}.lock"):
        yield


@contextmanager
def harness_lock(project_root: Path) -> Iterator[None]:
    """Linearize project-wide stop-file changes with final task transitions."""
    with _named_lock(project_root, ".harness.lock"):
        yield


@contextmanager
def configuration_lock(project_root: Path) -> Iterator[None]:
    """Serialize configuration replacement with work authorized by that configuration."""
    with _named_lock(project_root, ".configuration.lock"):
        yield


def is_external_action_marker(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().upper()
    return any(normalized.startswith(marker) for marker in EXTERNAL_ACTION_MARKERS)


def has_external_action_marker(value: str | None, marker: str) -> bool:
    return value is not None and value.strip().upper().startswith(marker)


def validate_artifacts(project_root: Path, artifacts: list[str] | None) -> list[str]:
    """Return canonical project-relative paths for artifacts that currently exist."""
    root = project_root.resolve(strict=True)
    normalized: list[str] = []
    for artifact in artifacts or []:
        if not artifact.strip() or "\x00" in artifact:
            raise ValueError("artifact paths must be non-empty and cannot contain NUL bytes")
        supplied = Path(artifact)
        candidate = supplied if supplied.is_absolute() else root / supplied
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise ValueError(f"artifact does not exist: {artifact}") from error
        try:
            relative = resolved.relative_to(root)
        except ValueError as error:
            raise PolicyBlockedError(f"artifact escapes the project root: {artifact}") from error
        if relative == Path():
            raise ValueError("the project root cannot be recorded as an artifact")
        value = relative.as_posix()
        if value not in normalized:
            normalized.append(value)
    return normalized


def qualifying_completion_artifacts(project_root: Path, artifacts: list[str]) -> list[str]:
    """Return existing regular project outputs that can substantiate completion."""
    normalized = validate_artifacts(project_root, artifacts)
    return [
        artifact
        for artifact in normalized
        if not Path(artifact).parts[0] == ".hexaharness" and (project_root / artifact).is_file()
    ]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_write_target(project_root: Path, target: str | Path) -> tuple[str, Path]:
    """Resolve an existing or prospective file target inside the project root."""
    root = project_root.resolve(strict=True)
    supplied = Path(target)
    candidate = supplied if supplied.is_absolute() else root / supplied
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise PolicyBlockedError(f"write target escapes the project root: {target}") from error
    if relative == Path():
        raise ValueError("the project root cannot be observed as a file write target")
    if resolved.exists() and not resolved.is_file():
        raise ValueError(f"write observation target is not a regular file: {target}")
    return relative.as_posix(), resolved


def _capture_pre_write_digest(path: Path) -> tuple[bool, str | None]:
    """Capture a stable regular-file digest, or absence, before an intended write."""
    try:
        before = path.stat()
    except FileNotFoundError:
        return False, None
    if not path.is_file():
        raise ValueError(f"write observation target is not a regular file: {path}")
    digest = _sha256_file(path)
    try:
        after = path.stat()
    except FileNotFoundError as error:
        raise ValueError(f"write target changed while it was observed: {path}") from error
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    ):
        raise ValueError(f"write target changed while it was observed: {path}")
    return True, digest


def _write_observation_event_exists(
    project_root: Path,
    observation: WriteObservation,
) -> bool:
    """Require the durable event paired with a task's pre-write snapshot."""
    expected = {
        "observation_id": observation.observation_id,
        "path": observation.path,
        "before_exists": observation.before_exists,
        "before_sha256": observation.before_sha256,
    }
    return any(
        event.event_type == "artifact.write-observed"
        and event.task_id == observation.task_id
        and event.timestamp >= observation.recorded_at
        and all(event.payload.get(key) == value for key, value in expected.items())
        for event in iter_events(project_root)
    )


def record_write_observation(
    project_root: Path,
    task_id: str,
    target: str | Path,
) -> WriteObservation:
    """Persist the required task-scoped snapshot before an allowed project write."""
    relative, path = _canonical_write_target(project_root, target)
    with task_lock(project_root, task_id):
        state = load_task(project_root, task_id)
        _ensure_mutable(state)
        can_observe_reconciliation = state.status in {
            TaskStatus.PAUSED,
            TaskStatus.ESCALATED,
        } and has_external_action_marker(state.next_step, RECONCILE_EXTERNAL_ACTION)
        if state.status != TaskStatus.ACTIVE and not can_observe_reconciliation:
            raise PolicyBlockedError(
                f"task {task_id} is {state.status.value}; writes cannot be observed"
            )
        before_exists, before_sha256 = _capture_pre_write_digest(path)
        observation = WriteObservation(
            observation_id=f"write-{secrets.token_hex(16)}",
            task_id=task_id,
            path=relative,
            before_exists=before_exists,
            before_sha256=before_sha256,
        )
        state.write_observations.append(observation)
        _save_task_unlocked(project_root, state)
        record_event(
            project_root,
            "artifact.write-observed",
            task_id=task_id,
            payload={
                "observation_id": observation.observation_id,
                "path": observation.path,
                "before_exists": observation.before_exists,
                "before_sha256": observation.before_sha256,
            },
        )
        return observation


def _file_evidence(project_root: Path, relative_path: str) -> dict[str, Any]:
    path = project_root / relative_path
    if path.is_file():
        return {
            "path": relative_path,
            "kind": "file",
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
    return {"path": relative_path, "kind": "directory"}


def _guidance_snapshot(project_root: Path) -> list[dict[str, Any]]:
    return [
        _file_evidence(project_root, relative_path)
        for relative_path in GUIDANCE_SNAPSHOT_PATHS
        if (project_root / relative_path).is_file()
    ]


def _stable_sha256(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def guidance_fingerprint(project_root: Path) -> str:
    """Bind evidence to the exact current configuration and agent guidance files."""
    return _stable_sha256(_guidance_snapshot(project_root))


def configuration_fingerprint(config: HarnessConfig) -> str:
    """Return a stable content digest for the validated harness configuration."""
    return _stable_sha256(config.model_dump(mode="json"))


def capture_artifact_evidence(
    project_root: Path,
    task_id: str,
    artifact: str,
    *,
    write_observation_id: str | None = None,
) -> ArtifactEvidence | None:
    """Capture task provenance for a regular file; directories are not evidence outputs."""
    normalized = validate_artifacts(project_root, [artifact])[0]
    path = project_root / normalized
    if not path.is_file():
        return None
    before = path.stat()
    sha256 = _sha256_file(path)
    after = path.stat()
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    ):
        raise ValueError(f"artifact changed while provenance was captured: {normalized}")
    return ArtifactEvidence(
        path=normalized,
        task_id=task_id,
        sha256=sha256,
        size_bytes=after.st_size,
        modified_at_ns=after.st_mtime_ns,
        write_observation_id=write_observation_id,
    )


def attach_artifact_evidence(
    project_root: Path,
    state: TaskState,
    artifacts: list[str],
) -> None:
    """Refresh the task's content-addressed evidence for the supplied artifact paths."""
    by_path = {item.path: item for item in state.artifact_evidence}
    for artifact in artifacts:
        normalized = validate_artifacts(project_root, [artifact])[0]
        observations = [
            item
            for item in state.write_observations
            if item.task_id == state.task_id and item.path == normalized
        ]
        observation = max(observations, key=lambda item: item.recorded_at, default=None)
        evidence = capture_artifact_evidence(
            project_root,
            state.task_id,
            normalized,
            write_observation_id=(observation.observation_id if observation is not None else None),
        )
        if evidence is not None:
            by_path[evidence.path] = evidence
    state.artifact_evidence = list(by_path.values())


def _matching_write_observation(
    project_root: Path,
    state: TaskState,
    evidence: ArtifactEvidence,
) -> WriteObservation | None:
    if evidence.write_observation_id is None:
        return None
    matching = [
        observation
        for observation in state.write_observations
        if observation.observation_id == evidence.write_observation_id
    ]
    if len(matching) != 1:
        return None
    observation = matching[0]
    if (
        observation.task_id != state.task_id
        or observation.path != evidence.path
        or observation.recorded_at < state.started_at
        or evidence.recorded_at < observation.recorded_at
        or observation.before_exists != (observation.before_sha256 is not None)
        or not _write_observation_event_exists(project_root, observation)
    ):
        return None
    return observation


def artifact_evidence_is_current(
    project_root: Path,
    state: TaskState,
    evidence: ArtifactEvidence,
    *,
    require_fresh: bool = False,
) -> bool:
    """Revalidate task ownership, content integrity, and a pre-write content delta."""
    if evidence.task_id != state.task_id or evidence.recorded_at < state.started_at:
        return False
    if require_fresh:
        observation = _matching_write_observation(project_root, state, evidence)
        if observation is None:
            return False
        if observation.before_exists and observation.before_sha256 == evidence.sha256:
            return False
    try:
        normalized = validate_artifacts(project_root, [evidence.path])[0]
    except (OSError, ValueError, PolicyBlockedError):
        return False
    path = project_root / normalized
    if normalized != evidence.path or not path.is_file():
        return False
    try:
        before = path.stat()
        digest = _sha256_file(path)
        after = path.stat()
        return (
            before.st_size == evidence.size_bytes
            and before.st_mtime_ns == evidence.modified_at_ns
            and before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
            and before.st_ctime_ns == after.st_ctime_ns
            and digest == evidence.sha256
        )
    except OSError:
        return False


def _has_phase_fresh_artifact_evidence(
    project_root: Path,
    state: TaskState,
    artifacts: list[str],
) -> bool:
    """Require a task-bound content delta observed after the current external phase began."""
    phase_started_at = state.external_action_phase_started_at
    if phase_started_at is None:
        return False
    by_path = {evidence.path: evidence for evidence in state.artifact_evidence}
    for artifact in artifacts:
        evidence = by_path.get(artifact)
        if evidence is None or evidence.recorded_at < phase_started_at:
            continue
        observation = _matching_write_observation(project_root, state, evidence)
        if observation is None or observation.recorded_at < phase_started_at:
            continue
        if artifact_evidence_is_current(project_root, state, evidence, require_fresh=True):
            return True
    return False


def qualifying_fresh_completion_artifacts(
    project_root: Path,
    state: TaskState,
) -> list[str]:
    """Return outputs with a task-scoped pre-write observation and content delta."""
    artifact_paths = set(state.artifacts)
    return [
        evidence.path
        for evidence in state.artifact_evidence
        if evidence.path in artifact_paths
        and Path(evidence.path).parts[0] != ".hexaharness"
        and artifact_evidence_is_current(project_root, state, evidence, require_fresh=True)
    ]


def fresh_completion_artifact_claims(
    project_root: Path,
    state: TaskState,
) -> set[tuple[str, str]]:
    """Return current path/content claims backed by this task's write observations."""
    qualifying = set(qualifying_fresh_completion_artifacts(project_root, state))
    return {
        (evidence.path, evidence.sha256)
        for evidence in state.artifact_evidence
        if evidence.path in qualifying
    }


def completion_has_unclaimed_artifact(project_root: Path, state: TaskState) -> bool:
    """Prevent one project write from being credited as multiple completed tasks."""
    current_claims = fresh_completion_artifact_claims(project_root, state)
    claimed_elsewhere: set[tuple[str, str]] = set()
    for other in list_tasks(project_root):
        if other.task_id == state.task_id or other.status != TaskStatus.COMPLETED:
            continue
        claimed_elsewhere.update(fresh_completion_artifact_claims(project_root, other))
    return bool(current_claims - claimed_elsewhere)


def sensor_result_is_current(
    project_root: Path,
    config: HarnessConfig,
    state: TaskState,
    result: SensorResult,
) -> bool:
    """Revalidate that a sensor result belongs to this task and the current harness."""
    if (
        result.task_id != state.task_id
        or result.recorded_at < state.started_at
        or result.config_fingerprint != configuration_fingerprint(config)
        or result.guidance_fingerprint != guidance_fingerprint(project_root)
        or result.output_sha256 is None
    ):
        return False
    try:
        output_path = validate_artifacts(project_root, [result.output_path])[0]
    except (OSError, ValueError, PolicyBlockedError):
        return False
    path = project_root / output_path
    try:
        return path.is_file() and _sha256_file(path) == result.output_sha256
    except OSError:
        return False


def required_sensor_evidence_is_current(
    project_root: Path,
    config: HarnessConfig,
    state: TaskState,
) -> bool:
    """Require every current required sensor to have current, task-bound passing evidence."""
    by_name = {result.name: result for result in state.sensor_results}
    return all(
        not sensor.required
        or (
            sensor.name in by_name
            and by_name[sensor.name].passed
            and sensor_result_is_current(project_root, config, state, by_name[sensor.name])
        )
        for sensor in config.sensors
    )


def make_task_id(goal: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp.lower()}-{slugify(goal, limit=28)}-{secrets.token_hex(2)}"


def _save_task_unlocked(project_root: Path, state: TaskState) -> None:
    validate_task_id(state.task_id)
    updated_at = utc_now()
    if updated_at <= state.updated_at:
        updated_at = state.updated_at + timedelta(microseconds=1)
    state.updated_at = updated_at
    paths = HarnessPaths(project_root)
    paths.states.mkdir(parents=True, exist_ok=True)
    payload = state.model_dump(mode="json")
    TaskState.model_validate(payload)
    write_json(paths.state_file(state.task_id), payload)


def save_task(project_root: Path, state: TaskState) -> None:
    """Persist same-status task data; status changes must use ``set_task_status``."""
    with task_lock(project_root, state.task_id):
        path = HarnessPaths(project_root).state_file(state.task_id)
        if path.is_file():
            current = TaskState.model_validate(read_json(path))
            if current.updated_at != state.updated_at:
                raise PolicyBlockedError(
                    "task checkpoint is stale; reload it before saving changes"
                )
            if current.status != state.status:
                raise PolicyBlockedError(
                    "task status changes must use the validated status transition API"
                )
            if (
                current.next_step != state.next_step
                or current.external_action_phase_started_at
                != state.external_action_phase_started_at
            ):
                raise PolicyBlockedError(
                    "pending steps and external-action phases must be changed through "
                    "the checkpoint workflow"
                )
        _save_task_unlocked(project_root, state)


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
    snapshot = _guidance_snapshot(project_root)
    fingerprint = _stable_sha256(snapshot)
    state = TaskState(
        task_id=make_task_id(goal),
        goal=goal,
        unattended=unattended,
        guidance_fingerprint=fingerprint,
    )
    save_task(project_root, state)
    record_event(
        project_root,
        "task.started",
        task_id=state.task_id,
        payload={
            "goal": goal,
            "unattended": unattended,
            "guidance_fingerprint": fingerprint,
            "guidance_snapshot": snapshot,
        },
    )
    return state


def _ensure_mutable(state: TaskState) -> None:
    if state.status in TERMINAL_STATUSES:
        raise PolicyBlockedError(
            f"task {state.task_id} is terminal ({state.status.value}) and cannot be changed"
        )


def checkpoint_task(
    project_root: Path,
    task_id: str,
    *,
    completed_step: str | None = None,
    next_step: str | None = None,
    clear_next: bool = False,
    resolve_external_action: bool = False,
    cancel_external_action: bool = False,
    artifacts: list[str] | None = None,
    tokens: int = 0,
    cost_usd: float = 0.0,
) -> TaskState:
    next_step_operations = sum(
        (next_step is not None, clear_next, resolve_external_action, cancel_external_action)
    )
    if next_step_operations > 1:
        raise ValueError(
            "--next, --clear-next, --resolve-external-action, and "
            "--cancel-external-action are mutually exclusive"
        )
    if tokens < 0 or cost_usd < 0:
        raise ValueError("token and cost increments cannot be negative")
    if next_step is not None and is_external_action_marker(next_step):
        raise PolicyBlockedError(
            "external-action checkpoints are runtime-managed; use prepare-external"
        )
    normalized_artifacts = validate_artifacts(project_root, artifacts)
    with task_lock(project_root, task_id):
        state = load_task(project_root, task_id)
        _ensure_mutable(state)
        reactivated = False
        previous_next_step = state.next_step
        attach_artifact_evidence(project_root, state, normalized_artifacts)
        if (
            next_step is not None
            and is_external_action_marker(previous_next_step)
            and next_step != previous_next_step
        ):
            raise PolicyBlockedError(
                "an external-action checkpoint cannot be replaced; verify it and use --clear-next"
            )
        if clear_next and previous_next_step is not None and not completed_step:
            raise PolicyBlockedError(
                "clearing a pending step requires a completed verification step"
            )
        if clear_next and is_external_action_marker(previous_next_step):
            if not has_external_action_marker(previous_next_step, VERIFY_EXTERNAL_ACTION_RESULT):
                raise PolicyBlockedError(
                    "only a post-action verification checkpoint can use --clear-next"
                )
            if not _has_phase_fresh_artifact_evidence(
                project_root,
                state,
                normalized_artifacts,
            ):
                raise PolicyBlockedError(
                    "clearing post-action verification requires task-attributed evidence "
                    "created or changed after the verification phase began"
                )
        if resolve_external_action:
            if not has_external_action_marker(previous_next_step, RECONCILE_EXTERNAL_ACTION):
                raise PolicyBlockedError(
                    "--resolve-external-action requires a reconciliation checkpoint"
                )
            if not completed_step or not _has_phase_fresh_artifact_evidence(
                project_root,
                state,
                normalized_artifacts,
            ):
                raise PolicyBlockedError(
                    "external-action reconciliation requires a resolution step and "
                    "task-attributed evidence created or changed after reconciliation began"
                )
            reactivated = _apply_status_transition(state, TaskStatus.ACTIVE, error=None)
        if cancel_external_action:
            if not has_external_action_marker(previous_next_step, PENDING_EXTERNAL_ACTION):
                raise PolicyBlockedError(
                    "--cancel-external-action requires a pending external-action checkpoint"
                )
            if not completed_step:
                raise PolicyBlockedError(
                    "cancelling a pending external action requires a recorded reason"
                )
            reactivated = _apply_status_transition(state, TaskStatus.ACTIVE, error=None)
        if completed_step and completed_step not in state.completed_steps:
            state.completed_steps.append(completed_step)
        if next_step is not None:
            state.next_step = next_step
        elif clear_next or resolve_external_action or cancel_external_action:
            state.next_step = None
            state.external_action_phase_started_at = None
        for artifact in normalized_artifacts:
            if artifact not in state.artifacts:
                state.artifacts.append(artifact)
        state.tokens_used += tokens
        state.cost_usd += cost_usd
        _save_task_unlocked(project_root, state)
        record_event(
            project_root,
            "task.checkpointed",
            task_id=task_id,
            payload={
                "completed_step": completed_step,
                "next_step": next_step,
                "next_step_cleared": clear_next or cancel_external_action,
                "external_action_resolved": resolve_external_action,
                "external_action_cancelled": cancel_external_action,
                "artifact_count": len(normalized_artifacts),
                "artifacts": [
                    _file_evidence(project_root, artifact) for artifact in normalized_artifacts
                ],
                "tokens_added": tokens,
                "cost_added_usd": cost_usd,
            },
        )
        if clear_next and has_external_action_marker(
            previous_next_step, VERIFY_EXTERNAL_ACTION_RESULT
        ):
            record_event(
                project_root,
                "external-action.verified",
                task_id=task_id,
                payload={"completed_step": completed_step},
            )
        if resolve_external_action:
            record_event(
                project_root,
                "external-action.reconciled",
                task_id=task_id,
                payload={"completed_step": completed_step},
            )
        if cancel_external_action:
            record_event(
                project_root,
                "external-action.cancelled",
                task_id=task_id,
                payload={"reason": completed_step},
            )
        if reactivated:
            record_event(project_root, "task.active", task_id=task_id)
        return state


def stage_external_action(project_root: Path, task_id: str, *, pending_step: str) -> TaskState:
    """Create the sole valid user-facing transition into an external-action checkpoint."""
    binding = EXTERNAL_ACTION_BINDING_PATTERN.search(pending_step)
    if not has_external_action_marker(pending_step, PENDING_EXTERNAL_ACTION) or binding is None:
        raise ValueError("prepare-external requires a one-time bound pending action")
    nonce = binding.group("nonce")
    with task_lock(project_root, task_id):
        state = load_task(project_root, task_id)
        _ensure_mutable(state)
        if state.status != TaskStatus.ACTIVE:
            raise PolicyBlockedError(
                f"task {task_id} is {state.status.value}; external action cannot be staged"
            )
        if state.next_step not in {None, pending_step}:
            raise PolicyBlockedError(
                "resolve the task's existing next step before staging an external action"
            )
        if nonce in state.external_action_nonces:
            raise PolicyBlockedError("external-action nonce has already been used by this task")
        state.next_step = pending_step
        state.external_action_phase_started_at = utc_now()
        state.external_action_nonces.append(nonce)
        _save_task_unlocked(project_root, state)
        record_event(
            project_root,
            "task.checkpointed",
            task_id=task_id,
            payload={"next_step": pending_step, "external_action_staged": True},
        )
        return state


def record_sensor_results(
    project_root: Path, task_id: str, results: list[SensorResult]
) -> TaskState:
    with task_lock(project_root, task_id):
        state = load_task(project_root, task_id)
        _ensure_mutable(state)
        state.sensor_results = results
        _save_task_unlocked(project_root, state)
        return state


def _validate_status_transition(current: TaskStatus, requested: TaskStatus) -> None:
    if current == requested:
        return
    if requested not in ALLOWED_STATUS_TRANSITIONS[current]:
        raise PolicyBlockedError(
            f"invalid task status transition: {current.value} -> {requested.value}"
        )


def _apply_status_transition(
    state: TaskState,
    status: TaskStatus,
    *,
    error: str | None,
) -> bool:
    _validate_status_transition(state.status, status)
    changed = state.status != status
    if not changed and state.status in TERMINAL_STATUSES:
        return False
    state.status = status
    state.last_error = error
    if status in TERMINAL_STATUSES:
        state.ended_at = utc_now()
    elif status == TaskStatus.ACTIVE:
        state.ended_at = None
    return changed


def prepare_external_action(
    project_root: Path,
    task_id: str,
    *,
    pending_step: str,
    reconcile_step: str,
) -> TaskState:
    """Atomically replace an exact pending action with crash-recovery state."""
    with task_lock(project_root, task_id):
        state = load_task(project_root, task_id)
        if state.status != TaskStatus.ACTIVE:
            raise PolicyBlockedError(
                f"task {task_id} is {state.status.value}; external action cannot start"
            )
        if state.next_step != pending_step:
            raise PolicyBlockedError(
                "approved external action does not match the durable pending checkpoint; "
                f"record this exact next step first: {pending_step}"
            )
        changed = _apply_status_transition(state, TaskStatus.PAUSED, error=None)
        state.next_step = reconcile_step
        state.external_action_phase_started_at = utc_now()
        _save_task_unlocked(project_root, state)
        if changed:
            record_event(project_root, "task.paused", task_id=task_id)
        return state


def mark_external_action_returned(
    project_root: Path,
    task_id: str,
    *,
    reconcile_step: str,
    verification_step: str,
) -> TaskState:
    """Atomically require post-action verification after an external command returns."""
    with task_lock(project_root, task_id):
        state = load_task(project_root, task_id)
        if state.status != TaskStatus.PAUSED or state.next_step != reconcile_step:
            raise PolicyBlockedError("external action is not in the expected reconciliation state")
        changed = _apply_status_transition(state, TaskStatus.ACTIVE, error=None)
        state.next_step = verification_step
        state.external_action_phase_started_at = utc_now()
        _save_task_unlocked(project_root, state)
        if changed:
            record_event(project_root, "task.active", task_id=task_id)
        return state


def set_task_status(
    project_root: Path,
    task_id: str,
    status: TaskStatus,
    *,
    error: str | None = None,
) -> TaskState:
    if status == TaskStatus.COMPLETED:
        raise PolicyBlockedError(
            "completion status can only be set by the verified completion workflow"
        )
    return _set_task_status(project_root, task_id, status, error=error)


def _set_task_status(
    project_root: Path,
    task_id: str,
    status: TaskStatus,
    *,
    error: str | None = None,
) -> TaskState:
    with task_lock(project_root, task_id):
        state = load_task(project_root, task_id)
        changed = _apply_status_transition(state, status, error=error)
        if not changed and state.status in TERMINAL_STATUSES:
            return state
        _save_task_unlocked(project_root, state)
        if changed:
            record_event(
                project_root,
                f"task.{status.value}",
                task_id=task_id,
                payload={"error": error} if error else {},
            )
        return state


def _mark_task_completed(project_root: Path, task_id: str) -> TaskState:
    return _set_task_status(project_root, task_id, TaskStatus.COMPLETED)
