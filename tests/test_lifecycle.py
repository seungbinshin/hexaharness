from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

from hexaharness.config import load_config, save_config
from hexaharness.errors import BudgetExceededError, PolicyBlockedError, VerificationFailedError
from hexaharness.events import iter_events
from hexaharness.models import CommandSpec, TaskKind, TaskStatus
from hexaharness.runner import new_external_action_step, run_task_command
from hexaharness.sensors import run_sensors
from hexaharness.state import (
    PENDING_EXTERNAL_ACTION,
    checkpoint_task,
    load_task,
    record_write_observation,
    save_task,
    stage_external_action,
    start_task,
    task_elapsed_seconds,
)
from hexaharness.workflow import complete_task, resume_task


def test_local_failure_can_be_repaired_without_resume(harness_project: Path) -> None:
    config = load_config(harness_project)
    command = [sys.executable, "check.py"]
    config.policy.allow_execute.append(command)
    save_config(harness_project, config)
    task = start_task(harness_project, "Repair a failing check")
    script = harness_project / "check.py"
    script.write_text("raise SystemExit(1)\n", encoding="utf-8")

    failed = run_task_command(harness_project, config, task_id=task.task_id, argv=command)
    assert failed.exit_code == 1
    assert failed.output_path and failed.output_path.endswith(".log")
    assert load_task(harness_project, task.task_id).status == TaskStatus.ACTIVE

    record_write_observation(harness_project, task.task_id, script.name)
    script.write_text("print('fixed')\n", encoding="utf-8")
    fixed = run_task_command(harness_project, config, task_id=task.task_id, argv=command)
    assert fixed.exit_code == 0
    completed = complete_task(harness_project, config, task.task_id, artifacts=[script.name])
    assert completed.status == TaskStatus.COMPLETED


def test_repeated_local_failures_still_escalate(harness_project: Path) -> None:
    config = load_config(harness_project)
    command = [sys.executable, "-c", "raise SystemExit(1)"]
    config.policy.allow_execute.append(command)
    save_config(harness_project, config)
    task = start_task(harness_project, "Bound a failed repair loop")
    for index in range(3):
        result = run_task_command(harness_project, config, task_id=task.task_id, argv=command)
        state = load_task(harness_project, task.task_id)
        assert state.status == (TaskStatus.ESCALATED if index == 2 else TaskStatus.ACTIVE)
    assert result.output_path and "escalation" in result.output_path


def test_different_checks_and_success_do_not_accumulate_one_error(harness_project: Path) -> None:
    config = load_config(harness_project)
    commands = [[sys.executable, "-c", f"print({i}); raise SystemExit(1)"] for i in range(3)]
    config.policy.allow_execute.extend(commands)
    save_config(harness_project, config)
    task = start_task(harness_project, "Handle independent check failures")
    for command in commands:
        run_task_command(harness_project, config, task_id=task.task_id, argv=command)
    assert load_task(harness_project, task.task_id).status == TaskStatus.ACTIVE
    run_task_command(harness_project, config, task_id=task.task_id, argv=config.project.test)
    run_task_command(harness_project, config, task_id=task.task_id, argv=commands[-1])
    assert load_task(harness_project, task.task_id).status == TaskStatus.ACTIVE


def test_approval_wait_survives_reload_without_consuming_execution_budget(
    harness_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(harness_project)
    config.budgets.max_wall_time_seconds = 30
    # This harmless inline command exercises the real human-gated protocol locally.
    command = [sys.executable, "-c", "print('approved action')"]
    save_config(harness_project, config)
    task = start_task(harness_project, "Wait for approval", kind=TaskKind.RELEASE)
    clock = task.started_at + timedelta(seconds=5)
    monkeypatch.setattr("hexaharness.state.utc_now", lambda: clock)
    pending = new_external_action_step(harness_project, task.task_id, command)
    stage_external_action(harness_project, task.task_id, pending_step=pending)
    clock += timedelta(days=2)
    assert task_elapsed_seconds(load_task(harness_project, task.task_id)) == pytest.approx(5)
    result = run_task_command(
        harness_project, config, task_id=task.task_id, argv=command, approved=True
    )
    assert result.exit_code == 0
    recovered = load_task(harness_project, task.task_id)
    assert recovered.approval_wait_seconds == pytest.approx(2 * 86400)
    assert task_elapsed_seconds(recovered) == pytest.approx(5)


def test_cancelling_approval_preserves_active_time(
    harness_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = start_task(harness_project, "Cancel publication")
    clock = task.started_at + timedelta(seconds=10)
    monkeypatch.setattr("hexaharness.state.utc_now", lambda: clock)
    pending = new_external_action_step(harness_project, task.task_id, ["git", "push"])
    stage_external_action(harness_project, task.task_id, pending_step=pending)
    clock += timedelta(hours=4)
    state = checkpoint_task(
        harness_project, task.task_id, completed_step="User declined", cancel_external_action=True
    )
    assert state.next_step is None
    assert task_elapsed_seconds(state) == pytest.approx(10)
    clock += timedelta(seconds=7)
    assert task_elapsed_seconds(load_task(harness_project, task.task_id)) == pytest.approx(17)


def test_budget_preflight_does_not_claim_an_external_action_started(harness_project: Path) -> None:
    config = load_config(harness_project)
    command = [sys.executable, "-c", "print('must not run')"]
    task = start_task(harness_project, "Preserve unexecuted approval")
    task.tool_calls = config.budgets.max_tool_calls
    save_task(harness_project, task)
    pending = new_external_action_step(harness_project, task.task_id, command)
    stage_external_action(harness_project, task.task_id, pending_step=pending)
    with pytest.raises(BudgetExceededError):
        run_task_command(harness_project, config, task_id=task.task_id, argv=command, approved=True)
    state = load_task(harness_project, task.task_id)
    assert state.next_step and state.next_step.startswith(PENDING_EXTERNAL_ACTION)
    assert not any(e.event_type == "external-action.started" for e in iter_events(harness_project))


def test_sensors_share_command_budgets_and_retain_partial_results(harness_project: Path) -> None:
    config = load_config(harness_project)
    config.budgets.max_tool_calls = 2
    config.sensors.append(CommandSpec(name="another", argv=config.project.test))
    save_config(harness_project, config)
    task = start_task(harness_project, "Bound all execution")
    run_task_command(harness_project, config, task_id=task.task_id, argv=config.project.test)
    with pytest.raises(BudgetExceededError, match="tool-call budget"):
        run_sensors(harness_project, config, task_id=task.task_id)
    state = load_task(harness_project, task.task_id)
    assert state.tool_calls == 2
    assert state.status == TaskStatus.ESCALATED
    assert [result.name for result in state.sensor_results] == ["test"]
    assert (harness_project / state.sensor_results[0].output_path).is_file()


@pytest.mark.parametrize("field", ["tokens_used", "cost_usd"])
def test_reported_budgets_block_successful_commands_and_sensors(
    harness_project: Path, field: str
) -> None:
    config = load_config(harness_project)
    for sensors in (False, True):
        task = start_task(harness_project, f"Bound reported {field} {sensors}")
        setattr(task, field, 1_000_000)
        save_task(harness_project, task)
        with pytest.raises(BudgetExceededError):
            if sensors:
                run_sensors(harness_project, config, task_id=task.task_id)
            else:
                run_task_command(
                    harness_project, config, task_id=task.task_id, argv=config.project.test
                )
        assert load_task(harness_project, task.task_id).tool_calls == 0


def test_expired_task_cannot_run_sensors_after_reactivation(harness_project: Path) -> None:
    config = load_config(harness_project)
    task = start_task(harness_project, "Keep elapsed budget on resume")
    task.started_at -= timedelta(hours=1)
    save_task(harness_project, task)
    with pytest.raises(BudgetExceededError, match="wall-time"):
        run_sensors(harness_project, config, task_id=task.task_id)
    resume_task(harness_project, task.task_id, reactivate=True)
    with pytest.raises(BudgetExceededError, match="wall-time"):
        run_sensors(harness_project, config, task_id=task.task_id)


def _write_report(project: Path, task_id: str, name: str) -> str:
    relative = f".hexaharness/artifacts/{task_id}/{name}"
    record_write_observation(project, task_id, relative)
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Observed outcome and supporting evidence.\n", encoding="utf-8")
    return relative


def test_review_can_complete_with_findings_without_modifying_source(harness_project: Path) -> None:
    task = start_task(harness_project, "Review existing project", kind=TaskKind.REVIEW)
    config = load_config(harness_project)
    with pytest.raises(VerificationFailedError):
        complete_task(harness_project, config, task.task_id)
    report = _write_report(harness_project, task.task_id, "review.md")
    checkpoint_task(
        harness_project, task.task_id, completed_step="Reviewed implementation", artifacts=[report]
    )
    completed = complete_task(harness_project, config, task.task_id)
    assert completed.status == TaskStatus.COMPLETED
    assert all(path.startswith(".hexaharness/") for path in completed.artifacts)


def test_release_requires_executed_and_verified_action(harness_project: Path) -> None:
    config = load_config(harness_project)
    task = start_task(harness_project, "Publish existing artifact", kind=TaskKind.RELEASE)
    report = _write_report(harness_project, task.task_id, "preflight.md")
    checkpoint_task(
        harness_project, task.task_id, completed_step="Checked release", artifacts=[report]
    )
    with pytest.raises(VerificationFailedError):
        complete_task(harness_project, config, task.task_id)
    command = [sys.executable, "-c", "print('simulated publication')"]
    pending = new_external_action_step(harness_project, task.task_id, command)
    stage_external_action(harness_project, task.task_id, pending_step=pending)
    run_task_command(harness_project, config, task_id=task.task_id, argv=command, approved=True)
    with pytest.raises(PolicyBlockedError):
        complete_task(harness_project, config, task.task_id)
    evidence = _write_report(harness_project, task.task_id, "verification.json")
    checkpoint_task(
        harness_project,
        task.task_id,
        completed_step="Verified published artifact",
        artifacts=[evidence],
        clear_next=True,
    )
    assert complete_task(harness_project, config, task.task_id).status == TaskStatus.COMPLETED


def test_review_kind_cannot_be_selected_after_start_to_weaken_completion(
    harness_project: Path,
) -> None:
    task = start_task(harness_project, "Produce a real change")
    task.kind = TaskKind.REVIEW
    with pytest.raises(PolicyBlockedError, match="kind"):
        save_task(harness_project, task)
