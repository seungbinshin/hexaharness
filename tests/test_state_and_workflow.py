from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from hexaharness.audit import audit_harness
from hexaharness.config import load_config, save_config
from hexaharness.errors import (
    HarnessStoppedError,
    PolicyBlockedError,
    TaskNotFoundError,
    VerificationFailedError,
)
from hexaharness.events import iter_events
from hexaharness.models import AuditStatus, CommandSpec, TaskStatus
from hexaharness.paths import HarnessPaths
from hexaharness.runner import (
    external_action_nonce,
    format_external_action_step,
    new_external_action_step,
)
from hexaharness.sensors import run_sensors
from hexaharness.state import (
    PENDING_EXTERNAL_ACTION,
    RECONCILE_EXTERNAL_ACTION,
    VERIFY_EXTERNAL_ACTION_RESULT,
    checkpoint_task,
    load_task,
    mark_external_action_returned,
    prepare_external_action,
    record_write_observation,
    required_sensor_evidence_is_current,
    save_task,
    set_task_status,
    stage_external_action,
    start_task,
    task_lock,
)
from hexaharness.workflow import complete_task, resume_task, stop_task_or_harness


def _concurrent_checkpoint(
    project_root: str,
    task_id: str,
    completed_step: str,
    start_gate: Any,
) -> None:
    start_gate.wait()
    checkpoint_task(
        Path(project_root),
        task_id,
        completed_step=completed_step,
        tokens=1,
    )


def _write_task_artifact(
    project_root: Path,
    task_id: str,
    path: str,
    content: str,
) -> Path:
    record_write_observation(project_root, task_id, path)
    artifact = project_root / path
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(content, encoding="utf-8")
    return artifact


def test_checkpoint_survives_reload(harness_project: Path) -> None:
    artifact = harness_project / "docs" / "map.md"
    artifact.parent.mkdir()
    artifact.write_text("mapping\n", encoding="utf-8")
    task = start_task(harness_project, "Build export")
    checkpoint_task(
        harness_project,
        task.task_id,
        completed_step="mapped fields",
        next_step="write exporter",
        artifacts=["docs/map.md"],
        tokens=42,
        cost_usd=0.25,
    )

    recovered = load_task(harness_project, task.task_id)
    assert recovered.completed_steps == ["mapped fields"]
    assert recovered.next_step == "write exporter"
    assert recovered.artifacts == ["docs/map.md"]
    assert recovered.tokens_used == 42
    assert recovered.cost_usd == pytest.approx(0.25)
    task_events = [event for event in iter_events(harness_project) if event.task_id == task.task_id]
    started = next(event for event in task_events if event.event_type == "task.started")
    snapshot = {item["path"]: item for item in started.payload["guidance_snapshot"]}
    assert snapshot[".hexaharness/harness.yaml"]["sha256"]
    checkpointed = next(event for event in task_events if event.event_type == "task.checkpointed")
    expected_digest = hashlib.sha256(b"mapping\n").hexdigest()
    assert checkpointed.payload["artifacts"] == [
        {
            "path": "docs/map.md",
            "kind": "file",
            "size_bytes": 8,
            "sha256": expected_digest,
        }
    ]


def test_legacy_task_state_without_external_nonce_history_still_loads(
    harness_project: Path,
) -> None:
    task = start_task(harness_project, "Load legacy task state")
    path = HarnessPaths(harness_project).state_file(task.task_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("external_action_phase_started_at")
    payload.pop("external_action_nonces")
    payload.pop("write_observations")
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_task(harness_project, task.task_id).external_action_nonces == []
    assert load_task(harness_project, task.task_id).write_observations == []


def test_complete_requires_passing_sensors(harness_project: Path) -> None:
    task = start_task(harness_project, "Verified task", unattended=True)
    _write_task_artifact(harness_project, task.task_id, "result.txt", "result\n")
    completed = complete_task(
        harness_project, load_config(harness_project), task.task_id, artifacts=["result.txt"]
    )
    assert completed.status == TaskStatus.COMPLETED
    assert completed.sensor_results[0].passed
    assert completed.sensor_results[0].task_id == task.task_id
    assert completed.sensor_results[0].config_fingerprint
    assert completed.sensor_results[0].guidance_fingerprint
    assert completed.sensor_results[0].output_sha256
    assert completed.artifacts == ["result.txt"]
    assert completed.artifact_evidence[0].task_id == task.task_id

    (harness_project / "CLAUDE.md").write_text("@AGENTS.md\n\nchanged guidance\n", encoding="utf-8")
    assert not required_sensor_evidence_is_current(
        harness_project,
        load_config(harness_project),
        completed,
    )


def test_run_sensors_rejects_a_stale_configuration_before_execution(
    harness_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = load_config(harness_project)
    current = load_config(harness_project)
    current.budgets.max_cost_usd += 1
    save_config(harness_project, current)

    monkeypatch.setattr(
        "hexaharness.sensors.execute_capture",
        lambda *args, **kwargs: pytest.fail("a stale sensor command was executed"),
    )
    with pytest.raises(PolicyBlockedError, match="configuration is stale"):
        run_sensors(harness_project, stale)


def test_completion_rechecks_configuration_after_sensor_evidence(
    harness_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(harness_project)
    task = start_task(harness_project, "Reject stale completion")
    artifact = _write_task_artifact(
        harness_project,
        task.task_id,
        "stale-completion.txt",
        "result\n",
    )
    from hexaharness import workflow

    real_save_task = workflow.save_task

    def save_then_replace_config(project_root: Path, state: Any) -> None:
        real_save_task(project_root, state)
        replacement = load_config(project_root)
        replacement.budgets.max_cost_usd += 1
        save_config(project_root, replacement)

    monkeypatch.setattr(workflow, "save_task", save_then_replace_config)

    with pytest.raises(PolicyBlockedError, match="configuration is stale"):
        complete_task(
            harness_project,
            config,
            task.task_id,
            artifacts=[artifact.name],
        )

    recovered = load_task(harness_project, task.task_id)
    assert recovered.status == TaskStatus.ACTIVE
    assert recovered.sensor_results


def test_final_completion_check_serializes_configuration_replacement(
    harness_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(harness_project)
    task = start_task(harness_project, "Serialize final configuration check")
    artifact = _write_task_artifact(
        harness_project,
        task.task_id,
        "serialized-completion.txt",
        "result\n",
    )
    from hexaharness import workflow

    original_check = workflow.ensure_configuration_current
    final_check_started = threading.Event()
    save_attempted = threading.Event()
    save_finished = threading.Event()
    check_count = 0

    def replace_config() -> None:
        final_check_started.wait(timeout=5)
        replacement = load_config(harness_project)
        replacement.budgets.max_cost_usd += 1
        save_attempted.set()
        save_config(harness_project, replacement)
        save_finished.set()

    writer = threading.Thread(target=replace_config)
    writer.start()

    def observed_check(project_root: Path, supplied: Any) -> None:
        nonlocal check_count
        check_count += 1
        if check_count == 2:
            final_check_started.set()
            assert save_attempted.wait(timeout=5)
            assert not save_finished.is_set()
        original_check(project_root, supplied)

    monkeypatch.setattr(workflow, "ensure_configuration_current", observed_check)
    completed = complete_task(
        harness_project,
        config,
        task.task_id,
        artifacts=[artifact.name],
    )
    writer.join(timeout=5)

    assert completed.status == TaskStatus.COMPLETED
    assert save_finished.is_set()


def test_failed_sensor_pauses_task(harness_project: Path) -> None:
    config = load_config(harness_project)
    failing_sensor = harness_project / "failing-sensor.py"
    failing_sensor.write_text("raise SystemExit(9)\n", encoding="utf-8")
    command = [config.project.test[0], failing_sensor.name]
    config.sensors = [CommandSpec(name="failure", argv=command, timeout_seconds=10)]
    config.policy.allow_execute = [command]
    save_config(harness_project, config)
    task = start_task(harness_project, "Failing task")
    _write_task_artifact(
        harness_project,
        task.task_id,
        "partial-result.txt",
        "partial\n",
    )
    checkpoint_task(harness_project, task.task_id, artifacts=["partial-result.txt"])

    with pytest.raises(VerificationFailedError):
        complete_task(harness_project, config, task.task_id)

    assert load_task(harness_project, task.task_id).status == TaskStatus.PAUSED
    sensor_tripwire = next(
        event
        for event in iter_events(harness_project)
        if event.event_type == "trip-wire.fired"
        and event.payload.get("name") == "sensor-regression"
    )
    assert sensor_tripwire.payload["response"] == "block-completion"


def test_complete_requires_an_existing_artifact_pointer(harness_project: Path) -> None:
    task = start_task(harness_project, "No output")

    with pytest.raises(VerificationFailedError, match="pre-write observation"):
        complete_task(harness_project, load_config(harness_project), task.task_id)


@pytest.mark.parametrize("artifact", [".hexaharness/GUIDES.md", "docs"])
def test_complete_rejects_bookkeeping_and_directory_only_artifacts(
    harness_project: Path, artifact: str
) -> None:
    (harness_project / "docs").mkdir()
    task = start_task(harness_project, "Non-output artifact")
    checkpoint_task(harness_project, task.task_id, artifacts=[artifact])

    with pytest.raises(VerificationFailedError, match=r"outside \.hexaharness"):
        complete_task(harness_project, load_config(harness_project), task.task_id)


def test_complete_rejects_an_unresolved_ordinary_next_step(harness_project: Path) -> None:
    task = start_task(harness_project, "Finish documentation")
    artifact = _write_task_artifact(
        harness_project,
        task.task_id,
        "draft.txt",
        "draft\n",
    )
    checkpoint_task(
        harness_project,
        task.task_id,
        next_step="finish documentation",
        artifacts=[artifact.name],
    )

    with pytest.raises(PolicyBlockedError, match="unresolved next step"):
        complete_task(harness_project, load_config(harness_project), task.task_id)

    checkpoint_task(
        harness_project,
        task.task_id,
        completed_step="finished documentation",
        clear_next=True,
    )
    completed = complete_task(harness_project, load_config(harness_project), task.task_id)
    assert completed.status == TaskStatus.COMPLETED


def test_completion_rejects_reusing_an_unchanged_preexisting_artifact(
    harness_project: Path,
) -> None:
    first = start_task(harness_project, "Create shared result", unattended=True)
    shared = _write_task_artifact(
        harness_project,
        first.task_id,
        "shared-result.txt",
        "one task output\n",
    )
    complete_task(
        harness_project,
        load_config(harness_project),
        first.task_id,
        artifacts=[shared.name],
    )

    second = start_task(harness_project, "Pretend shared result is new", unattended=True)
    record_write_observation(harness_project, second.task_id, shared.name)
    with pytest.raises(VerificationFailedError, match="content or existence delta"):
        complete_task(
            harness_project,
            load_config(harness_project),
            second.task_id,
            artifacts=[shared.name],
        )


def test_touch_or_future_mtime_cannot_fake_three_unattended_runs(
    harness_project: Path,
) -> None:
    shared = harness_project / "preexisting-readme.md"
    shared.write_text("unchanged content\n", encoding="utf-8")
    original_content = shared.read_bytes()
    tasks = [
        start_task(harness_project, f"No-work task {index}", unattended=True) for index in range(3)
    ]
    for task in tasks:
        record_write_observation(harness_project, task.task_id, shared.name)

    future = time.time_ns() + 3_600_000_000_000
    os.utime(shared, ns=(future, future))
    assert shared.read_bytes() == original_content

    for task in tasks:
        with pytest.raises(VerificationFailedError, match="content or existence delta"):
            complete_task(
                harness_project,
                load_config(harness_project),
                task.task_id,
                artifacts=[shared.name],
            )

    unattended_check = audit_harness(harness_project)[11]
    assert unattended_check.status == AuditStatus.WARN
    assert unattended_check.evidence == "0 evidence-complete unattended runs"


def test_one_content_delta_cannot_be_credited_to_multiple_tasks(
    harness_project: Path,
) -> None:
    shared = harness_project / "shared-concurrent-result.md"
    shared.write_text("before\n", encoding="utf-8")
    tasks = [
        start_task(harness_project, f"Concurrent claimant {index}", unattended=True)
        for index in range(3)
    ]
    for task in tasks:
        record_write_observation(harness_project, task.task_id, shared.name)
    shared.write_text("one shared content change\n", encoding="utf-8")

    first = complete_task(
        harness_project,
        load_config(harness_project),
        tasks[0].task_id,
        artifacts=[shared.name],
    )
    assert first.status == TaskStatus.COMPLETED
    for task in tasks[1:]:
        with pytest.raises(VerificationFailedError, match="not already used"):
            complete_task(
                harness_project,
                load_config(harness_project),
                task.task_id,
                artifacts=[shared.name],
            )

    unattended_check = audit_harness(harness_project)[11]
    assert unattended_check.status == AuditStatus.WARN
    assert unattended_check.evidence == "1 evidence-complete unattended runs"


def test_stop_preserves_state_and_resume_clears_global_stop(harness_project: Path) -> None:
    task = start_task(harness_project, "Recoverable task")
    stopped = stop_task_or_harness(harness_project, task_id=task.task_id, reason="operator review")
    assert stopped is not None and stopped.status == TaskStatus.STOPPED

    stop_task_or_harness(harness_project, task_id=None, reason="incident")
    assert HarnessPaths(harness_project).stop_file.is_file()
    resumed = resume_task(harness_project, task.task_id, clear_stop=True)
    assert resumed.status == TaskStatus.STOPPED
    assert not HarnessPaths(harness_project).stop_file.exists()
    assert any(
        event.event_type == "task.resumed" and event.task_id == task.task_id
        for event in iter_events(harness_project)
    )


def test_concurrent_checkpoints_are_serialized_across_processes(
    harness_project: Path,
) -> None:
    task = start_task(harness_project, "Concurrent updates")
    context = multiprocessing.get_context("spawn")
    start_gate = context.Event()
    processes = [
        context.Process(
            target=_concurrent_checkpoint,
            args=(str(harness_project), task.task_id, f"step-{index}", start_gate),
        )
        for index in range(4)
    ]
    for process in processes:
        process.start()
    start_gate.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    recovered = load_task(harness_project, task.task_id)
    assert set(recovered.completed_steps) == {f"step-{index}" for index in range(4)}
    assert recovered.tokens_used == 4


def test_task_lock_is_safely_reentrant(harness_project: Path) -> None:
    task = start_task(harness_project, "Nested lock")

    with task_lock(harness_project, task.task_id):
        checkpoint_task(harness_project, task.task_id, completed_step="nested")

    assert load_task(harness_project, task.task_id).completed_steps == ["nested"]


def test_stale_direct_save_cannot_overwrite_a_newer_checkpoint(harness_project: Path) -> None:
    task = start_task(harness_project, "Optimistic state guard")
    first = load_task(harness_project, task.task_id)
    stale = load_task(harness_project, task.task_id)
    first.tokens_used = 1
    save_task(harness_project, first)
    stale.tokens_used = 99

    with pytest.raises(PolicyBlockedError, match="stale"):
        save_task(harness_project, stale)

    assert load_task(harness_project, task.task_id).tokens_used == 1


def test_terminal_tasks_reject_mutation_and_transition(harness_project: Path) -> None:
    task = start_task(harness_project, "Terminal task")
    stop_task_or_harness(harness_project, task_id=task.task_id, reason="done")

    with pytest.raises(PolicyBlockedError, match="only active tasks"):
        complete_task(harness_project, load_config(harness_project), task.task_id)
    with pytest.raises(PolicyBlockedError, match="invalid task status transition"):
        set_task_status(harness_project, task.task_id, TaskStatus.ACTIVE)
    with pytest.raises(PolicyBlockedError, match="terminal"):
        checkpoint_task(harness_project, task.task_id, completed_step="too late")


def test_generic_status_api_cannot_bypass_verified_completion(harness_project: Path) -> None:
    task = start_task(harness_project, "Bypass completion")

    with pytest.raises(PolicyBlockedError, match="verified completion workflow"):
        set_task_status(harness_project, task.task_id, TaskStatus.COMPLETED)

    assert load_task(harness_project, task.task_id).status == TaskStatus.ACTIVE


def test_external_action_checkpoint_must_be_verified_before_completion(
    harness_project: Path,
) -> None:
    task = start_task(harness_project, "Release")
    with pytest.raises(PolicyBlockedError, match="runtime-managed"):
        checkpoint_task(
            harness_project,
            task.task_id,
            next_step=f"{VERIFY_EXTERNAL_ACTION_RESULT} git push origin main",
        )
    with pytest.raises(PolicyBlockedError, match="prepare-external"):
        checkpoint_task(
            harness_project,
            task.task_id,
            next_step=f"{PENDING_EXTERNAL_ACTION} git push origin main",
        )
    command = ["git", "push", "origin", "main"]
    pending_step = new_external_action_step(harness_project, task.task_id, command)
    stage_external_action(harness_project, task.task_id, pending_step=pending_step)

    with pytest.raises(PolicyBlockedError, match="external action"):
        complete_task(harness_project, load_config(harness_project), task.task_id)

    (harness_project / "pending-evidence.txt").write_text("pending\n", encoding="utf-8")
    with pytest.raises(PolicyBlockedError, match="post-action verification"):
        checkpoint_task(
            harness_project,
            task.task_id,
            completed_step="pretend complete",
            clear_next=True,
            artifacts=["pending-evidence.txt"],
        )
    with pytest.raises(PolicyBlockedError, match="cannot be replaced"):
        checkpoint_task(harness_project, task.task_id, next_step="skip verification")

    reconcile_step = format_external_action_step(
        harness_project,
        task.task_id,
        RECONCILE_EXTERNAL_ACTION,
        command,
        nonce=external_action_nonce(pending_step),
    )
    verification_step = format_external_action_step(
        harness_project,
        task.task_id,
        VERIFY_EXTERNAL_ACTION_RESULT,
        command,
        nonce=external_action_nonce(pending_step),
    )
    stale_evidence = _write_task_artifact(
        harness_project,
        task.task_id,
        "pre-transition-evidence.txt",
        "not post-action evidence\n",
    )
    prepare_external_action(
        harness_project,
        task.task_id,
        pending_step=pending_step,
        reconcile_step=reconcile_step,
    )
    mark_external_action_returned(
        harness_project,
        task.task_id,
        reconcile_step=reconcile_step,
        verification_step=verification_step,
    )
    with pytest.raises(PolicyBlockedError, match="after the verification phase began"):
        checkpoint_task(
            harness_project,
            task.task_id,
            completed_step="claimed stale verification",
            clear_next=True,
            artifacts=[stale_evidence.name],
        )
    with pytest.raises(PolicyBlockedError, match="completed verification"):
        checkpoint_task(harness_project, task.task_id, clear_next=True)
    _write_task_artifact(
        harness_project,
        task.task_id,
        "verification.txt",
        "verified\n",
    )
    cleared = checkpoint_task(
        harness_project,
        task.task_id,
        completed_step="verified remote commit",
        clear_next=True,
        artifacts=["verification.txt"],
    )
    assert cleared.next_step is None


def test_external_action_nonce_cannot_be_reused_after_reconciliation(
    harness_project: Path,
) -> None:
    task = start_task(harness_project, "Reconcile one-time action")
    command = ["release-tool", "publish"]
    pending_step = new_external_action_step(harness_project, task.task_id, command)
    nonce = external_action_nonce(pending_step)
    stage_external_action(harness_project, task.task_id, pending_step=pending_step)
    reconcile_step = format_external_action_step(
        harness_project,
        task.task_id,
        RECONCILE_EXTERNAL_ACTION,
        command,
        nonce=nonce,
    )
    stale_evidence = _write_task_artifact(
        harness_project,
        task.task_id,
        "pre-reconciliation-evidence.txt",
        "not reconciliation evidence\n",
    )
    prepare_external_action(
        harness_project,
        task.task_id,
        pending_step=pending_step,
        reconcile_step=reconcile_step,
    )
    with pytest.raises(PolicyBlockedError, match="after reconciliation began"):
        checkpoint_task(
            harness_project,
            task.task_id,
            completed_step="claimed stale reconciliation",
            resolve_external_action=True,
            artifacts=[stale_evidence.name],
        )
    evidence = _write_task_artifact(
        harness_project,
        task.task_id,
        "reconciled-action.txt",
        "confirmed action did not complete\n",
    )
    resolved = checkpoint_task(
        harness_project,
        task.task_id,
        completed_step="confirmed action did not complete",
        resolve_external_action=True,
        artifacts=[evidence.name],
    )

    assert resolved.status == TaskStatus.ACTIVE
    assert resolved.next_step is None
    assert resolved.external_action_nonces == [nonce]
    with pytest.raises(PolicyBlockedError, match="nonce has already been used"):
        stage_external_action(harness_project, task.task_id, pending_step=pending_step)


def test_pending_external_action_can_be_cancelled_without_reusing_nonce(
    harness_project: Path,
) -> None:
    task = start_task(harness_project, "Declined publication")
    command = ["release-tool", "publish"]
    pending_step = new_external_action_step(harness_project, task.task_id, command)
    stage_external_action(harness_project, task.task_id, pending_step=pending_step)

    with pytest.raises(PolicyBlockedError, match="recorded reason"):
        checkpoint_task(
            harness_project,
            task.task_id,
            cancel_external_action=True,
        )

    cancelled = checkpoint_task(
        harness_project,
        task.task_id,
        completed_step="publication declined by the user",
        cancel_external_action=True,
    )

    assert cancelled.status == TaskStatus.ACTIVE
    assert cancelled.next_step is None
    assert cancelled.external_action_phase_started_at is None
    assert cancelled.external_action_nonces == [external_action_nonce(pending_step)]
    assert any(
        event.event_type == "external-action.cancelled"
        and event.task_id == task.task_id
        and event.payload["reason"] == "publication declined by the user"
        for event in iter_events(harness_project)
    )
    with pytest.raises(PolicyBlockedError, match="nonce has already been used"):
        stage_external_action(harness_project, task.task_id, pending_step=pending_step)


def test_external_action_cancellation_only_accepts_pending_phase(
    harness_project: Path,
) -> None:
    task = start_task(harness_project, "Do not cancel recovery")
    with pytest.raises(PolicyBlockedError, match="pending external-action checkpoint"):
        checkpoint_task(
            harness_project,
            task.task_id,
            completed_step="not pending",
            cancel_external_action=True,
        )

    command = ["release-tool", "publish"]
    pending_step = new_external_action_step(harness_project, task.task_id, command)
    nonce = external_action_nonce(pending_step)
    reconcile_step = format_external_action_step(
        harness_project,
        task.task_id,
        RECONCILE_EXTERNAL_ACTION,
        command,
        nonce=nonce,
    )
    verification_step = format_external_action_step(
        harness_project,
        task.task_id,
        VERIFY_EXTERNAL_ACTION_RESULT,
        command,
        nonce=nonce,
    )
    stage_external_action(harness_project, task.task_id, pending_step=pending_step)
    prepare_external_action(
        harness_project,
        task.task_id,
        pending_step=pending_step,
        reconcile_step=reconcile_step,
    )
    with pytest.raises(PolicyBlockedError, match="pending external-action checkpoint"):
        checkpoint_task(
            harness_project,
            task.task_id,
            completed_step="do not skip reconciliation",
            cancel_external_action=True,
        )

    mark_external_action_returned(
        harness_project,
        task.task_id,
        reconcile_step=reconcile_step,
        verification_step=verification_step,
    )
    with pytest.raises(PolicyBlockedError, match="pending external-action checkpoint"):
        checkpoint_task(
            harness_project,
            task.task_id,
            completed_step="do not skip verification",
            cancel_external_action=True,
        )


def test_artifacts_must_exist_inside_the_project(harness_project: Path) -> None:
    task = start_task(harness_project, "Artifact validation")
    outside = harness_project.parent / "outside-result.txt"
    outside.write_text("outside\n", encoding="utf-8")

    with pytest.raises(ValueError, match="does not exist"):
        checkpoint_task(harness_project, task.task_id, artifacts=["missing.txt"])
    with pytest.raises(PolicyBlockedError, match="escapes"):
        checkpoint_task(harness_project, task.task_id, artifacts=[str(outside)])

    symlink = harness_project / "escaped-link.txt"
    symlink.symlink_to(outside)
    with pytest.raises(PolicyBlockedError, match="escapes"):
        checkpoint_task(harness_project, task.task_id, artifacts=[symlink.name])


def test_global_stop_blocks_completion(harness_project: Path) -> None:
    task = start_task(harness_project, "Stopped completion")
    stop_task_or_harness(harness_project, task_id=None, reason="incident")

    with pytest.raises(HarnessStoppedError):
        complete_task(harness_project, load_config(harness_project), task.task_id)

    assert load_task(harness_project, task.task_id).status == TaskStatus.ACTIVE


def test_invalid_resume_cannot_clear_the_global_stop(harness_project: Path) -> None:
    stop_task_or_harness(harness_project, task_id=None, reason="incident")

    with pytest.raises(TaskNotFoundError, match="task checkpoint not found"):
        resume_task(harness_project, "missing-task", clear_stop=True)

    assert HarnessPaths(harness_project).stop_file.is_file()
