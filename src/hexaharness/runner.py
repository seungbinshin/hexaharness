from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from hexaharness.config import ensure_configuration_current, ensure_configuration_ready
from hexaharness.errors import BudgetExceededError, HarnessStoppedError, PolicyBlockedError
from hexaharness.events import record_event
from hexaharness.execution import execute_capture
from hexaharness.io import redact_argv, redact_text_using_argv, write_json
from hexaharness.models import (
    CommandResult,
    EscalationPacket,
    HarnessConfig,
    PolicyDecision,
    TaskState,
    TaskStatus,
)
from hexaharness.paths import HarnessPaths
from hexaharness.policy import evaluate_command
from hexaharness.state import (
    PENDING_EXTERNAL_ACTION,
    RECONCILE_EXTERNAL_ACTION,
    VERIFY_EXTERNAL_ACTION_RESULT,
    checkpoint_task,
    configuration_lock,
    harness_lock,
    has_external_action_marker,
    load_task,
    mark_external_action_returned,
    prepare_external_action,
    save_task,
    set_task_status,
    task_lock,
)
from hexaharness.tripwires import active_trip_wires

EXTERNAL_ACTION_TOKEN_PATTERN = re.compile(
    r"\[action-nonce:([0-9a-f]{32});argv-hmac-sha256:([0-9a-f]{64})\]$"
)


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
    alternatives_tested: list[str] | None = None,
    cost_of_waiting: str | None = None,
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
        alternatives_tested=alternatives_tested or ["bounded retries were exhausted"],
        safest_default="Preserve current state and make no external changes.",
        cost_of_waiting=cost_of_waiting
        or "The requested outcome remains unavailable while the task is blocked.",
        evidence_paths=evidence_paths,
    )
    artifact_dir = HarnessPaths(project_root).task_artifacts(task_id)
    path = artifact_dir / "escalation.json"
    sequence = 2
    while path.exists():
        path = artifact_dir / f"escalation-{sequence}.json"
        sequence += 1
    write_json(path, packet.model_dump(mode="json"))
    return path.relative_to(project_root).as_posix()


def escalate_task(
    project_root: Path,
    task_id: str,
    *,
    reason: str,
    evidence_paths: list[str],
    alternatives_tested: list[str] | None = None,
    cost_of_waiting: str | None = None,
    error: str | None = None,
) -> tuple[TaskState, str]:
    """Persist a structured escalation and move a nonterminal task to escalated."""
    with task_lock(project_root, task_id):
        escalation_path = _write_escalation(
            project_root,
            task_id,
            reason=reason,
            evidence_paths=list(dict.fromkeys(evidence_paths)),
            alternatives_tested=alternatives_tested,
            cost_of_waiting=cost_of_waiting,
        )
        checkpoint_task(project_root, task_id, artifacts=[escalation_path])
        state = set_task_status(project_root, task_id, TaskStatus.ESCALATED, error=error or reason)
        record_event(
            project_root,
            "task.escalation-created",
            task_id=task_id,
            payload={"path": escalation_path, "reason": reason},
        )
        return state, escalation_path


def _raise_budget_escalation(
    project_root: Path,
    task_id: str,
    *,
    reason: str,
    evidence_paths: list[str],
) -> NoReturn:
    _, escalation_path = escalate_task(
        project_root,
        task_id,
        reason=reason,
        evidence_paths=evidence_paths,
        alternatives_tested=["the configured execution bound was preserved"],
        cost_of_waiting="Further work is blocked until the budget or task scope is reviewed.",
    )
    record_event(
        project_root,
        "budget.exhausted",
        task_id=task_id,
        payload={"reason": reason, "escalation_path": escalation_path},
    )
    raise BudgetExceededError(f"{reason}; escalation packet: {escalation_path}")


def _external_action_key(project_root: Path) -> bytes:
    key_path = HarnessPaths(project_root).external_action_key
    with harness_lock(project_root):
        if key_path.is_file():
            if os.name != "nt" and stat.S_IMODE(key_path.stat().st_mode) & 0o077:
                raise ValueError("external-action binding key permissions must be owner-only")
            key = key_path.read_bytes()
            if len(key) != 32:
                raise ValueError("external-action binding key is invalid")
            return key
        key = secrets.token_bytes(32)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(key_path, flags, 0o600)
        try:
            os.write(descriptor, key)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return key


def external_action_nonce(step: str | None) -> str:
    if step is None or (match := EXTERNAL_ACTION_TOKEN_PATTERN.search(step)) is None:
        raise PolicyBlockedError("external action is missing its one-time binding")
    return match.group(1)


def format_external_action_step(
    project_root: Path,
    task_id: str,
    prefix: str,
    argv: list[str],
    *,
    nonce: str,
) -> str:
    """Bind a redacted checkpoint display to exact argv with a local keyed digest."""
    if not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("external-action nonce must be 16 lowercase hexadecimal bytes")
    serialized = json.dumps(
        {"argv": argv, "nonce": nonce, "phase": prefix, "task_id": task_id},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hmac.new(
        _external_action_key(project_root), serialized.encode(), hashlib.sha256
    ).hexdigest()
    return (
        f"{prefix} {shlex.join(redact_argv(argv))} [action-nonce:{nonce};argv-hmac-sha256:{digest}]"
    )


def new_external_action_step(project_root: Path, task_id: str, argv: list[str]) -> str:
    return format_external_action_step(
        project_root,
        task_id,
        PENDING_EXTERNAL_ACTION,
        argv,
        nonce=secrets.token_hex(16),
    )


def _run_task_command_locked(
    project_root: Path,
    config: HarnessConfig,
    *,
    task_id: str,
    argv: list[str],
    approved: bool = False,
    reviewed: bool = False,
    retries: int = 0,
    timeout_seconds: int | None = None,
) -> CommandResult:
    ensure_configuration_current(project_root, config)
    ensure_configuration_ready(config, project_root)
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
        _raise_budget_escalation(
            project_root,
            task_id,
            reason=(
                f"requested retries ({retries}) exceed max_retries_per_step "
                f"({config.budgets.max_retries_per_step})"
            ),
            evidence_paths=state.artifacts,
        )

    outcome = evaluate_command(config, argv, project_root=project_root)
    redacted_argv = redact_argv(argv)
    policy_reason = redact_text_using_argv(outcome.reason, argv)
    record_event(
        project_root,
        "policy.command",
        task_id=task_id,
        payload={
            "decision": outcome.decision.value,
            "reason": policy_reason,
            "argv": redacted_argv,
            "approved": approved,
            "reviewed": reviewed,
        },
    )
    if outcome.decision == PolicyDecision.DENY:
        raise PolicyBlockedError(f"command denied: {policy_reason}")
    if has_external_action_marker(state.next_step, PENDING_EXTERNAL_ACTION):
        nonce = external_action_nonce(state.next_step)
        exact_pending_step = format_external_action_step(
            project_root,
            task_id,
            PENDING_EXTERNAL_ACTION,
            argv,
            nonce=nonce,
        )
        if state.next_step != exact_pending_step:
            raise PolicyBlockedError(
                "command does not match the task's durable pending external action; "
                "cancel or execute that exact staged action first"
            )
        if outcome.decision != PolicyDecision.ASK or not outcome.requires_human_approval:
            raise PolicyBlockedError(
                "prepared external-action policy changed; cancel the pending action with "
                "--cancel-external-action and restage it under the current policy"
            )
    if outcome.decision == PolicyDecision.ASK:
        if outcome.requires_human_approval and not approved:
            raise PolicyBlockedError(
                f"command requires explicit human approval: {policy_reason}; rerun with "
                "--approved only after approval is obtained"
            )
        if not outcome.requires_human_approval and not (reviewed or approved):
            raise PolicyBlockedError(
                f"unregistered command requires agent review: {policy_reason}; inspect the exact "
                "argv and rerun with --reviewed only for authorized project-local reversible work"
            )

    human_gated_action = (
        outcome.decision == PolicyDecision.ASK and outcome.requires_human_approval and approved
    )
    reconcile_step: str | None = None
    if human_gated_action:
        if retries:
            raise PolicyBlockedError(
                "human-gated external actions cannot be retried automatically; use --retries 0"
            )
        nonce = external_action_nonce(state.next_step)
        pending_step = format_external_action_step(
            project_root, task_id, PENDING_EXTERNAL_ACTION, argv, nonce=nonce
        )
        reconcile_step = format_external_action_step(
            project_root, task_id, RECONCILE_EXTERNAL_ACTION, argv, nonce=nonce
        )
        state = prepare_external_action(
            project_root,
            task_id,
            pending_step=pending_step,
            reconcile_step=reconcile_step,
        )
        record_event(
            project_root,
            "external-action.started",
            task_id=task_id,
            payload={"argv": redacted_argv, "recovery": reconcile_step},
        )

    evidence: list[str] = []
    total_duration = 0.0
    last_exit_code: int | None = None
    last_summary = ""
    last_timed_out = False
    last_stopped = False
    attempts_made = 0

    for attempt in range(retries + 1):
        if paths.stop_file.exists():
            state = set_task_status(
                project_root,
                task_id,
                TaskStatus.PAUSED,
                error="emergency stop activated during command execution",
            )
            raise HarnessStoppedError(
                "emergency stop became active; remaining command attempts were cancelled"
            )
        remaining = _remaining_wall_time(config, state.started_at)
        if remaining <= 0:
            _raise_budget_escalation(
                project_root,
                task_id,
                reason="task wall-time budget is exhausted",
                evidence_paths=[*state.artifacts, *evidence],
            )
        if state.tool_calls >= config.budgets.max_tool_calls:
            _raise_budget_escalation(
                project_root,
                task_id,
                reason="task tool-call budget is exhausted",
                evidence_paths=[*state.artifacts, *evidence],
            )
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
        last_stopped = captured.stopped
        succeeded = captured.exit_code == 0 and not captured.timed_out and not captured.stopped
        if succeeded:
            state.last_error = None
            save_task(project_root, state)
            if human_gated_action:
                if reconcile_step is None:  # pragma: no cover - guarded by construction
                    raise RuntimeError("external action recovery checkpoint is missing")
                verification_step = format_external_action_step(
                    project_root,
                    task_id,
                    VERIFY_EXTERNAL_ACTION_RESULT,
                    argv,
                    nonce=external_action_nonce(reconcile_step),
                )
                state = mark_external_action_returned(
                    project_root,
                    task_id,
                    reconcile_step=reconcile_step,
                    verification_step=verification_step,
                )
                record_event(
                    project_root,
                    "external-action.execution-returned",
                    task_id=task_id,
                    payload={"argv": redacted_argv, "next_step": verification_step},
                )
            record_event(
                project_root,
                "command.succeeded",
                task_id=task_id,
                payload={
                    "argv": redacted_argv,
                    "attempt": attempt + 1,
                    "duration_seconds": captured.duration_seconds,
                    "output_path": captured.output_path,
                },
            )
            return CommandResult(
                task_id=task_id,
                argv=redacted_argv,
                decision=outcome.decision,
                exit_code=captured.exit_code,
                attempts=attempts_made,
                duration_seconds=total_duration,
                output_path=captured.output_path,
                summary=captured.summary,
                timed_out=False,
                stopped=False,
            )

        if captured.stopped:
            state.last_error = captured.summary
            save_task(project_root, state)
            state = set_task_status(
                project_root,
                task_id,
                TaskStatus.PAUSED,
                error=captured.summary,
            )
            record_event(
                project_root,
                "command.stopped",
                task_id=task_id,
                payload={
                    "argv": redacted_argv,
                    "attempt": attempt + 1,
                    "output_path": captured.output_path,
                },
            )
            return CommandResult(
                task_id=task_id,
                argv=redacted_argv,
                decision=outcome.decision,
                exit_code=captured.exit_code,
                attempts=attempts_made,
                duration_seconds=total_duration,
                output_path=captured.output_path,
                summary=captured.summary,
                timed_out=False,
                stopped=True,
            )

        fingerprint = _fingerprint(argv, captured.exit_code, captured.timed_out)
        state.last_error = captured.summary
        save_task(project_root, state)
        record_event(
            project_root,
            "command.failed",
            task_id=task_id,
            payload={
                "argv": redacted_argv,
                "attempt": attempt + 1,
                "exit_code": captured.exit_code,
                "timed_out": captured.timed_out,
                "fingerprint": fingerprint,
                "output_path": captured.output_path,
            },
        )
        fired = active_trip_wires(project_root, config, state, error_fingerprint=fingerprint)
        if fired:
            state = set_task_status(
                project_root,
                task_id,
                TaskStatus.PAUSED,
                error=captured.summary,
            )
            break

    reason = "command failed after bounded attempts"
    _, escalation_path = escalate_task(
        project_root,
        task_id,
        reason=reason,
        evidence_paths=[*state.artifacts, *evidence],
        error=last_summary,
    )
    return CommandResult(
        task_id=task_id,
        argv=redacted_argv,
        decision=outcome.decision,
        exit_code=last_exit_code,
        attempts=attempts_made,
        duration_seconds=total_duration,
        output_path=escalation_path,
        summary=last_summary,
        timed_out=last_timed_out,
        stopped=last_stopped,
    )


def run_task_command(
    project_root: Path,
    config: HarnessConfig,
    *,
    task_id: str,
    argv: list[str],
    approved: bool = False,
    reviewed: bool = False,
    retries: int = 0,
    timeout_seconds: int | None = None,
) -> CommandResult:
    with task_lock(project_root, task_id):
        with configuration_lock(project_root):
            return _run_task_command_locked(
                project_root,
                config,
                task_id=task_id,
                argv=argv,
                approved=approved,
                reviewed=reviewed,
                retries=retries,
                timeout_seconds=timeout_seconds,
            )
