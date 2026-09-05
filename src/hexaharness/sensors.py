from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

from hexaharness.config import ensure_configuration_current, ensure_configuration_ready
from hexaharness.errors import HarnessStoppedError, PolicyBlockedError
from hexaharness.events import record_event
from hexaharness.execution import execute_capture
from hexaharness.models import CommandSpec, HarnessConfig, PolicyDecision, SensorResult, TaskStatus
from hexaharness.paths import HarnessPaths
from hexaharness.policy import evaluate_command
from hexaharness.runner import execution_time_remaining
from hexaharness.state import (
    capture_artifact_evidence,
    configuration_fingerprint,
    configuration_lock,
    guidance_fingerprint,
    is_external_action_marker,
    load_task,
    record_sensor_results,
    save_task,
    set_task_status,
    task_lock,
)


def select_sensors(config: HarnessConfig, names: list[str] | None) -> list[CommandSpec]:
    if not names:
        return config.sensors
    requested = set(names)
    selected = [sensor for sensor in config.sensors if sensor.name in requested]
    missing = requested - {sensor.name for sensor in selected}
    if missing:
        raise ValueError(f"unknown sensors: {', '.join(sorted(missing))}")
    return selected


def _run_sensors_locked(
    project_root: Path,
    config: HarnessConfig,
    *,
    task_id: str | None = None,
    names: list[str] | None = None,
) -> list[SensorResult]:
    ensure_configuration_current(project_root, config)
    ensure_configuration_ready(config, project_root)
    if HarnessPaths(project_root).stop_file.exists():
        raise HarnessStoppedError("emergency stop is active; verification is blocked")
    artifact_task = task_id or "verification"
    config_digest = configuration_fingerprint(config)
    guidance_digest = guidance_fingerprint(project_root)
    results: list[SensorResult] = []
    for sensor in select_sensors(config, names):
        if HarnessPaths(project_root).stop_file.exists():
            raise HarnessStoppedError(
                "emergency stop became active; remaining sensors were cancelled"
            )
        state = load_task(project_root, task_id) if task_id else None
        timeout = sensor.timeout_seconds
        if state is not None:
            if state.status != TaskStatus.ACTIVE or is_external_action_marker(state.next_step):
                raise PolicyBlockedError(
                    "sensors require an active task with no pending external action"
                )
            timeout = min(timeout, execution_time_remaining(project_root, config, state))
        outcome = evaluate_command(config, sensor.argv, project_root=project_root)
        if outcome.decision != PolicyDecision.ALLOW:
            record_event(
                project_root,
                "sensor.blocked",
                task_id=task_id,
                payload={
                    "name": sensor.name,
                    "decision": outcome.decision.value,
                    "reason": outcome.reason,
                },
            )
            raise PolicyBlockedError(
                f"sensor {sensor.name!r} is not allow-listed: {outcome.reason}"
            )
        captured = execute_capture(
            project_root,
            task_id=artifact_task,
            label=f"sensor-{sensor.name}",
            argv=sensor.argv,
            timeout_seconds=timeout,
        )
        if state is not None:
            state.tool_calls += 1
            state.attempts += 1
            save_task(project_root, state)
        output_evidence = capture_artifact_evidence(
            project_root,
            artifact_task,
            captured.output_path,
        )
        result = SensorResult(
            name=sensor.name,
            passed=(captured.exit_code == 0 and not captured.timed_out and not captured.stopped),
            exit_code=captured.exit_code,
            duration_seconds=captured.duration_seconds,
            output_path=captured.output_path,
            summary=captured.summary,
            timed_out=captured.timed_out,
            stopped=captured.stopped,
            task_id=task_id,
            config_fingerprint=config_digest,
            guidance_fingerprint=guidance_digest,
            output_sha256=(output_evidence.sha256 if output_evidence is not None else None),
        )
        results.append(result)
        record_event(
            project_root,
            "sensor.completed",
            task_id=task_id,
            payload={
                "name": sensor.name,
                "passed": result.passed,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "stopped": result.stopped,
                "duration_seconds": result.duration_seconds,
                "output_path": result.output_path,
            },
        )
        if task_id is not None:
            record_sensor_results(project_root, task_id, results)
            if captured.stopped:
                set_task_status(project_root, task_id, TaskStatus.PAUSED, error=captured.summary)
                break
    return results


def run_sensors(
    project_root: Path,
    config: HarnessConfig,
    *,
    task_id: str | None = None,
    names: list[str] | None = None,
) -> list[SensorResult]:
    """Run sensors only while their supplied configuration remains the durable one."""
    with task_lock(project_root, task_id) if task_id else nullcontext():
        with configuration_lock(project_root):
            return _run_sensors_locked(
                project_root,
                config,
                task_id=task_id,
                names=names,
            )


def required_sensors_passed(config: HarnessConfig, results: list[SensorResult]) -> bool:
    result_by_name = {result.name: result for result in results}
    return all(
        not sensor.required
        or (sensor.name in result_by_name and result_by_name[sensor.name].passed)
        for sensor in config.sensors
    )
