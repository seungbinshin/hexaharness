from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hexaharness.config import load_config, save_config
from hexaharness.errors import HarnessStoppedError, PolicyBlockedError
from hexaharness.models import TaskStatus
from hexaharness.paths import HarnessPaths
from hexaharness.runner import run_task_command
from hexaharness.state import load_task, start_task
from hexaharness.workflow import stop_task_or_harness


def test_allowed_command_runs_and_records_evidence(harness_project: Path) -> None:
    task = start_task(harness_project, "Run command")
    result = run_task_command(
        harness_project,
        load_config(harness_project),
        task_id=task.task_id,
        argv=[sys.executable, "-c", "print('done')"],
    )

    assert result.exit_code == 0
    assert result.attempts == 1
    assert "done" in result.summary
    assert result.output_path is not None
    assert (harness_project / result.output_path).is_file()
    assert load_task(harness_project, task.task_id).tool_calls == 1


def test_unknown_command_needs_approval(harness_project: Path) -> None:
    config = load_config(harness_project)
    config.policy.allow_execute = []
    save_config(harness_project, config)
    task = start_task(harness_project, "Approval")
    command = [sys.executable, "-c", "print('approved')"]

    with pytest.raises(PolicyBlockedError, match="requires explicit approval"):
        run_task_command(harness_project, config, task_id=task.task_id, argv=command)
    approved = run_task_command(
        harness_project,
        config,
        task_id=task.task_id,
        argv=command,
        approved=True,
    )
    assert approved.exit_code == 0


def test_denied_command_cannot_be_approved(harness_project: Path) -> None:
    config = load_config(harness_project)
    config.policy.deny_execute = [[sys.executable]]
    task = start_task(harness_project, "Denied")
    with pytest.raises(PolicyBlockedError, match="denied"):
        run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=[sys.executable, "-V"],
            approved=True,
        )


def test_failed_command_escalates_after_bounded_retries(harness_project: Path) -> None:
    task = start_task(harness_project, "Escalate")
    result = run_task_command(
        harness_project,
        load_config(harness_project),
        task_id=task.task_id,
        argv=[sys.executable, "-c", "raise SystemExit(7)"],
        retries=1,
    )
    state = load_task(harness_project, task.task_id)

    assert result.exit_code == 7
    assert result.attempts == 2
    assert state.status == TaskStatus.ESCALATED
    assert result.output_path is not None
    assert result.output_path.endswith("escalation.json")


def test_secret_bearing_arguments_are_redacted(harness_project: Path) -> None:
    task = start_task(harness_project, "Redaction")
    result = run_task_command(
        harness_project,
        load_config(harness_project),
        task_id=task.task_id,
        argv=[sys.executable, "-c", "print('ok')", "--token", "hunter2"],
    )
    assert "hunter2" not in " ".join(result.argv)
    assert result.output_path is not None
    log = (harness_project / result.output_path).read_text(encoding="utf-8")
    assert "hunter2" not in log
    assert "<redacted>" in log


def test_global_stop_blocks_execution(harness_project: Path) -> None:
    task = start_task(harness_project, "Blocked")
    stop_task_or_harness(harness_project, task_id=None, reason="incident")
    with pytest.raises(HarnessStoppedError):
        run_task_command(
            harness_project,
            load_config(harness_project),
            task_id=task.task_id,
            argv=[sys.executable, "-V"],
        )
    assert HarnessPaths(harness_project).state_file(task.task_id).is_file()


def test_missing_command_is_captured_as_exit_127(harness_project: Path) -> None:
    config = load_config(harness_project)
    config.policy.allow_execute.append(["definitely-not-a-real-command"])
    task = start_task(harness_project, "Missing binary")
    result = run_task_command(
        harness_project,
        config,
        task_id=task.task_id,
        argv=["definitely-not-a-real-command"],
    )
    assert result.exit_code == 127
    assert "command not found" in result.summary
