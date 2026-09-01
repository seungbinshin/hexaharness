from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from hexaharness.errors import BudgetExceededError, HarnessStoppedError, PolicyBlockedError
from hexaharness.events import record_event
from hexaharness.execution import execute_capture
from hexaharness.io import redact_argv, write_json
from hexaharness.models import (
    CommandResult,
    EscalationPacket,
    HarnessConfig,
    PolicyDecision,
    TaskStatus,
)
from hexaharness.paths import HarnessPaths
from hexaharness.policy import evaluate_command
from hexaharness.state import load_task, save_task, set_task_status
from hexaharness.tripwires import active_trip_wires


def _remaining_wall_time(config: HarnessConfig, started_at: datetime) -> int:
    elapsed = (datetime.now(UTC) - started_at).total_seconds()
    return max(0, int(config.budgets.max_wall_time_seconds - elapsed))


def _fingerprint(argv: list[str], exit_code: int | None, timed_out: bool) -> str:
    material = f"{argv[0]}\0{exit_code}\0{timed_out}"
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _write_escalation(
    project_root: Path,
    task_id: str,
    *,
    reason: str,
    evidence_paths: list[str],
) -> str:
    packet = EscalationPacket(
        task_id=task_id,
        reason=reason,
        decision_required=(
            "Choose whether to change the task, policy, command, or acceptance criteria."
        ),
        recommended_action=(
            "Inspect the captured evidence and address the root failure before retrying."
        ),
        alternatives_tested=["bounded retries were exhausted"],
        safest_default="Preserve current state and make no external changes.",
        evidence_paths=evidence_paths,
    )
    path = HarnessPaths(project_root).task_artifacts(task_id) / "escalation.json"
    write_json(path, packet.model_dump(mode="json"))
    return path.relative_to(project_root).as_posix()


def run_task_command(
    project_root: Path,
    config: HarnessConfig,
    *,
    task_id: str,
    argv: list[str],
    approved: bool = False,
    retries: int = 0,
    timeout_seconds: int | None = None,
) -> CommandResult:
    paths = HarnessPaths(project_root)
    if paths.stop_file.exists():
        raise HarnessStoppedError(
            f"emergency stop is active: remove it with `hexa resume {task_id} --clear-stop`"
        )
    state = load_task(project_root, task_id)
    if state.status != TaskStatus.ACTIVE:
        raise PolicyBlockedError(
            f"task {task_id} is {state.status.value}; review it and use `hexa resume --reactivate`"
        )
    if retries > config.budgets.max_retries_per_step:
        raise BudgetExceededError(
            f"requested retries ({retries}) exceed max_retries_per_step "
            f"({config.budgets.max_retries_per_step})"
        )

    outcome = evaluate_command(config, argv)
    record_event(
        project_root,
        "policy.command",
        task_id=task_id,
        payload={
            "decision": outcome.decision.value,
            "reason": outcome.reason,
            "argv": redact_argv(argv),
            "approved": approved,
        },
    )
    if outcome.decision == PolicyDecision.DENY:
        raise PolicyBlockedError(f"command denied: {outcome.reason}")
    if outcome.decision == PolicyDecision.ASK and not approved:
        raise PolicyBlockedError(
            f"command requires explicit approval: {outcome.reason}; rerun with --approved only "
            "after approval is obtained"
        )

    evidence: list[str] = []
    total_duration = 0.0
    last_exit_code: int | None = None
    last_summary = ""
    last_timed_out = False
    attempts_made = 0

    for attempt in range(retries + 1):
        remaining = _remaining_wall_time(config, state.started_at)
        if remaining <= 0:
            raise BudgetExceededError("task wall-time budget is exhausted")
        if state.tool_calls >= config.budgets.max_tool_calls:
            raise BudgetExceededError("task tool-call budget is exhausted")
        execution_timeout = min(timeout_seconds or remaining, remaining)
        captured = execute_capture(
            project_root,
            task_id=task_id,
            label=f"command-attempt-{attempt + 1}",
            argv=argv,
            timeout_seconds=max(1, execution_timeout),
        )
        attempts_made += 1
        state.attempts += 1
        state.tool_calls += 1
        total_duration += captured.duration_seconds
        evidence.append(captured.output_path)
        last_exit_code = captured.exit_code
        last_summary = captured.summary
        last_timed_out = captured.timed_out
        succeeded = captured.exit_code == 0 and not captured.timed_out
        if succeeded:
            state.last_error = None
            save_task(project_root, state)
            record_event(
                project_root,
                "command.succeeded",
                task_id=task_id,
                payload={
                    "argv": redact_argv(argv),
                    "attempt": attempt + 1,
                    "duration_seconds": captured.duration_seconds,
                    "output_path": captured.output_path,
                },
            )
            return CommandResult(
                task_id=task_id,
                argv=redact_argv(argv),
                decision=outcome.decision,
                exit_code=captured.exit_code,
                attempts=attempts_made,
                duration_seconds=total_duration,
                output_path=captured.output_path,
                summary=captured.summary,
                timed_out=False,
            )

        fingerprint = _fingerprint(argv, captured.exit_code, captured.timed_out)
        state.last_error = captured.summary
        save_task(project_root, state)
        record_event(
            project_root,
            "command.failed",
            task_id=task_id,
            payload={
                "argv": redact_argv(argv),
                "attempt": attempt + 1,
                "exit_code": captured.exit_code,
                "timed_out": captured.timed_out,
                "fingerprint": fingerprint,
                "output_path": captured.output_path,
            },
        )
        fired = active_trip_wires(project_root, config, state, error_fingerprint=fingerprint)
        if fired:
            state.status = TaskStatus.PAUSED
            save_task(project_root, state)
            break

    reason = "command failed after bounded attempts"
    escalation_path = _write_escalation(
        project_root, task_id, reason=reason, evidence_paths=evidence
    )
    set_task_status(project_root, task_id, TaskStatus.ESCALATED, error=last_summary)
    record_event(
        project_root,
        "task.escalation-created",
        task_id=task_id,
        payload={"path": escalation_path, "reason": reason},
    )
    return CommandResult(
        task_id=task_id,
        argv=redact_argv(argv),
        decision=outcome.decision,
        exit_code=last_exit_code,
        attempts=attempts_made,
        duration_seconds=total_duration,
        output_path=escalation_path,
        summary=last_summary,
        timed_out=last_timed_out,
    )
