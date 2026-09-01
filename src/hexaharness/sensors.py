from __future__ import annotations

from pathlib import Path

from hexaharness.events import record_event
from hexaharness.execution import execute_capture
from hexaharness.models import CommandSpec, HarnessConfig, SensorResult


def select_sensors(config: HarnessConfig, names: list[str] | None) -> list[CommandSpec]:
    if not names:
        return config.sensors
    requested = set(names)
    selected = [sensor for sensor in config.sensors if sensor.name in requested]
    missing = requested - {sensor.name for sensor in selected}
    if missing:
        raise ValueError(f"unknown sensors: {', '.join(sorted(missing))}")
    return selected


def run_sensors(
    project_root: Path,
    config: HarnessConfig,
    *,
    task_id: str | None = None,
    names: list[str] | None = None,
) -> list[SensorResult]:
    artifact_task = task_id or "verification"
    results: list[SensorResult] = []
    for sensor in select_sensors(config, names):
        captured = execute_capture(
            project_root,
            task_id=artifact_task,
            label=f"sensor-{sensor.name}",
            argv=sensor.argv,
            timeout_seconds=sensor.timeout_seconds,
        )
        result = SensorResult(
            name=sensor.name,
            passed=captured.exit_code == 0 and not captured.timed_out,
            exit_code=captured.exit_code,
            duration_seconds=captured.duration_seconds,
            output_path=captured.output_path,
            summary=captured.summary,
            timed_out=captured.timed_out,
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
                "duration_seconds": result.duration_seconds,
                "output_path": result.output_path,
            },
        )
    return results


def required_sensors_passed(config: HarnessConfig, results: list[SensorResult]) -> bool:
    result_by_name = {result.name: result for result in results}
    return all(
        not sensor.required
        or (sensor.name in result_by_name and result_by_name[sensor.name].passed)
        for sensor in config.sensors
    )
