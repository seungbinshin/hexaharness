from __future__ import annotations

from pathlib import Path

from hexaharness.config import ensure_configuration_current
from hexaharness.errors import HarnessStoppedError, PolicyBlockedError, VerificationFailedError
from hexaharness.events import record_event
from hexaharness.io import atomic_write_text
from hexaharness.models import HarnessConfig, TaskKind, TaskState, TaskStatus
from hexaharness.paths import HarnessPaths
from hexaharness.sensors import required_sensors_passed, run_sensors
from hexaharness.state import (
    _mark_task_completed,
    attach_artifact_evidence,
    completion_has_unclaimed_artifact,
    configuration_lock,
    harness_lock,
    is_external_action_marker,
    load_task,
    qualifying_task_outputs,
    record_sensor_results,
    required_sensor_evidence_is_current,
    save_task,
    sensor_result_is_current,
    set_task_status,
    task_lock,
    validate_artifacts,
)
from hexaharness.tripwires import active_trip_wires


def complete_task(
    project_root: Path,
    config: HarnessConfig,
    task_id: str,
    *,
    artifacts: list[str] | None = None,
) -> TaskState:
    with task_lock(project_root, task_id):
        with configuration_lock(project_root):
            ensure_configuration_current(project_root, config)
        state = load_task(project_root, task_id)
        if state.status != TaskStatus.ACTIVE:
            raise PolicyBlockedError(
                f"task {task_id} is {state.status.value}; only active tasks can be completed"
            )
        if HarnessPaths(project_root).stop_file.exists():
            raise HarnessStoppedError("emergency stop is active; completion is blocked")
        if is_external_action_marker(state.next_step):
            raise PolicyBlockedError(
                "external action is pending execution, reconciliation, or verification; resolve "
                "the recorded checkpoint with evidence before completion"
            )
        if state.next_step is not None:
            raise PolicyBlockedError(
                "task still has an unresolved next step; record it completed and use "
                "checkpoint --clear-next before completion"
            )
        normalized_artifacts = validate_artifacts(project_root, artifacts)
        candidate_artifacts = validate_artifacts(
            project_root, [*state.artifacts, *normalized_artifacts]
        )
        state.artifacts = candidate_artifacts
        attach_artifact_evidence(project_root, state, normalized_artifacts)
        candidate_artifact_evidence = list(state.artifact_evidence)
        if not qualifying_task_outputs(project_root, state):
            if state.kind == TaskKind.CHANGE:
                raise VerificationFailedError(
                    "completion requires at least one current artifact outside .hexaharness with "
                    "a task-scoped pre-write observation and a content or existence delta"
                )
            raise VerificationFailedError(
                "completion requires a fresh findings report for review tasks or "
                "verified external-action evidence for release tasks"
            )

        results = run_sensors(project_root, config, task_id=task_id)
        state = record_sensor_results(project_root, task_id, results)
        if not required_sensors_passed(config, results) or not required_sensor_evidence_is_current(
            project_root, config, state
        ):
            result_by_name = {result.name: result for result in results}
            required_failures = sum(
                1
                for sensor in config.sensors
                if sensor.required
                and (
                    sensor.name not in result_by_name
                    or not result_by_name[sensor.name].passed
                    or not sensor_result_is_current(
                        project_root,
                        config,
                        state,
                        result_by_name[sensor.name],
                    )
                )
            )
            active_trip_wires(
                project_root,
                config,
                state,
                required_sensor_failures=required_failures,
            )
            set_task_status(
                project_root,
                task_id,
                TaskStatus.PAUSED,
                error="required computational sensors lacked current passing evidence",
            )
            raise VerificationFailedError(
                "required computational sensors lacked current passing evidence"
            )

        state.artifacts = validate_artifacts(project_root, candidate_artifacts)
        state.artifact_evidence = candidate_artifact_evidence
        save_task(project_root, state)
        with configuration_lock(project_root):
            with harness_lock(project_root):
                ensure_configuration_current(project_root, config)
                if HarnessPaths(project_root).stop_file.exists():
                    raise HarnessStoppedError(
                        "emergency stop became active during verification; completion is blocked"
                    )
                state = load_task(project_root, task_id)
                if not qualifying_task_outputs(
                    project_root, state
                ) or not required_sensor_evidence_is_current(project_root, config, state):
                    raise VerificationFailedError(
                        "completion evidence changed after verification; rerun current sensors"
                    )
                if state.kind == TaskKind.CHANGE and not completion_has_unclaimed_artifact(
                    project_root, state
                ):
                    raise VerificationFailedError(
                        "completion requires at least one artifact content claim not already used "
                        "by another completed task"
                    )
                return _mark_task_completed(project_root, task_id)


def stop_task_or_harness(
    project_root: Path,
    *,
    task_id: str | None,
    reason: str,
) -> TaskState | None:
    if task_id:
        return set_task_status(project_root, task_id, TaskStatus.STOPPED, error=reason)
    paths = HarnessPaths(project_root)
    with harness_lock(project_root):
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
    with task_lock(project_root, task_id):
        state = load_task(project_root, task_id)
        if clear_stop:
            with harness_lock(project_root):
                if paths.stop_file.exists():
                    paths.stop_file.unlink()
                    record_event(project_root, "harness.stop-cleared", task_id=task_id)
        if reactivate and state.status in {TaskStatus.PAUSED, TaskStatus.ESCALATED}:
            state = set_task_status(project_root, task_id, TaskStatus.ACTIVE)
            record_event(project_root, "task.reactivated", task_id=task_id)
        record_event(
            project_root,
            "task.resumed",
            task_id=task_id,
            payload={
                "status": state.status.value,
                "has_next_step": state.next_step is not None,
                "clear_stop_requested": clear_stop,
                "reactivate_requested": reactivate,
            },
        )
        return state
