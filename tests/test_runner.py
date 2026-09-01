from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hexaharness.config import load_config, save_config
from hexaharness.errors import BudgetExceededError, HarnessStoppedError, PolicyBlockedError
from hexaharness.models import TaskStatus
from hexaharness.paths import HarnessPaths
from hexaharness.runner import (
    external_action_nonce,
    format_external_action_step,
    new_external_action_step,
    run_task_command,
)
from hexaharness.state import (
    PENDING_EXTERNAL_ACTION,
    RECONCILE_EXTERNAL_ACTION,
    VERIFY_EXTERNAL_ACTION_RESULT,
    checkpoint_task,
    load_task,
    record_write_observation,
    stage_external_action,
    start_task,
)
from hexaharness.workflow import stop_task_or_harness


def test_allowed_command_runs_and_records_evidence(harness_project: Path) -> None:
    task = start_task(harness_project, "Run command")
    config = load_config(harness_project)
    command = [sys.executable, "-c", "print('done')"]
    config.policy.allow_execute.append(command)
    save_config(harness_project, config)
    result = run_task_command(
        harness_project,
        config,
        task_id=task.task_id,
        argv=command,
    )

    assert result.exit_code == 0
    assert result.attempts == 1
    assert "done" in result.summary
    assert result.output_path is not None
    assert (harness_project / result.output_path).is_file()
    assert load_task(harness_project, task.task_id).tool_calls == 1


def test_stale_configuration_cannot_authorize_a_command(harness_project: Path) -> None:
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('stale-command-ran').write_text('ran')",
    ]
    stale = load_config(harness_project)
    stale.policy.allow_execute.append(command)
    save_config(harness_project, stale)
    stale = load_config(harness_project)

    current = load_config(harness_project)
    current.policy.deny_execute.insert(0, command)
    save_config(harness_project, current)
    task = start_task(harness_project, "Reject stale command policy")

    with pytest.raises(PolicyBlockedError, match="configuration is stale"):
        run_task_command(harness_project, stale, task_id=task.task_id, argv=command)

    assert not (harness_project / "stale-command-ran").exists()
    assert load_task(harness_project, task.task_id).tool_calls == 0


def test_unknown_local_command_needs_agent_review(harness_project: Path) -> None:
    config = load_config(harness_project)
    task = start_task(harness_project, "Approval")
    command = ["true"]

    with pytest.raises(PolicyBlockedError, match="requires agent review"):
        run_task_command(harness_project, config, task_id=task.task_id, argv=command)
    reviewed = run_task_command(
        harness_project,
        config,
        task_id=task.task_id,
        argv=command,
        reviewed=True,
    )
    assert reviewed.exit_code == 0


@pytest.mark.parametrize(
    "command",
    [
        ["make", "deploy"],
        ["kubectl", "apply", "-f", "deployment.yaml"],
        ["terraform", "apply", "saved.tfplan"],
        ["aws", "s3", "cp", "artifact.whl", "s3://release-bucket/artifact.whl"],
        ["aws", "route53", "change-resource-record-sets", "--hosted-zone-id", "Z1"],
        ["timeout", "30", "kubectl", "apply", "-f", "deployment.yaml"],
        ["nice", "-n", "5", "terraform", "apply", "saved.tfplan"],
        ["xargs", "kubectl", "apply", "-f", "deployment.yaml"],
        ["busybox", "sh", "-c", "git push origin main"],
        ["toybox", "sh", "-c", "git push origin main"],
        ["busybox", "git", "push", "origin", "main"],
        ["lua", "-e", "os.execute('git push origin main')"],
        ["stdbuf", "-oL", "git", "push", "origin", "main"],
        ["nohup", "git", "push", "origin", "main"],
        ["setsid", "-f", "git", "push", "origin", "main"],
        ["ionice", "-c2", "git", "push", "origin", "main"],
        ["taskset", "0x1", "git", "push", "origin", "main"],
        ["strace", "-o", "trace.log", "git", "push", "origin", "main"],
        ["uv", "run", "git", "push", "origin", "main"],
        ["npx", "--yes", "git", "push", "origin", "main"],
        ["python", "-m", "twine", "upload", "dist/pkg.whl"],
        ["pypy3", "-c", "run_external_action()"],
        ["python3", "-Ic", "run_external_action()"],
        ["nodejs", "-e", "runExternalAction()"],
        ["py", "-c", "run_external_action()"],
        ["npm", "exec", "--call", "gh pr merge 123"],
        ["npx", "--call", "gh pr merge 123"],
        ["opaque-interpreter", "-e", "run_external_action()"],
        ["deploy", "production"],
    ],
)
def test_agent_review_cannot_authorize_an_external_action(
    harness_project: Path, command: list[str]
) -> None:
    config = load_config(harness_project)
    task = start_task(harness_project, "Publish")

    with pytest.raises(PolicyBlockedError, match="requires explicit human approval"):
        run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=command,
            reviewed=True,
        )


def test_denied_command_cannot_be_approved(harness_project: Path) -> None:
    config = load_config(harness_project)
    command = ["false"]
    config.policy.deny_execute = [command]
    save_config(harness_project, config)
    task = start_task(harness_project, "Denied")
    with pytest.raises(PolicyBlockedError, match="denied"):
        run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=command,
            approved=True,
        )


@pytest.mark.parametrize(
    "command",
    [
        ["busybox", "rm", "-rf", "src"],
        ["toybox", "rm", "--recursive", "--force", "src"],
        ["env", "busybox", "rm", "-fr", "src"],
        ["stdbuf", "-oL", "rm", "-rf", "src"],
        ["strace", "-o", "trace.log", "rm", "-rf", "src"],
        ["uv", "run", "rm", "-rf", "src"],
        ["unknown-launcher", "rm", "-rf", "src"],
        ["git", "reset", "--har", "HEAD"],
        ["rm", "--recurs", "--for", "src"],
    ],
)
def test_multicall_destructive_command_cannot_be_approved(
    harness_project: Path, command: list[str]
) -> None:
    config = load_config(harness_project)
    task = start_task(harness_project, "Reject multicall destruction")

    with pytest.raises(PolicyBlockedError, match="denied"):
        run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=command,
            approved=True,
        )


def test_failed_command_escalates_after_bounded_retries(harness_project: Path) -> None:
    task = start_task(harness_project, "Escalate")
    config = load_config(harness_project)
    command = [sys.executable, "-c", "raise SystemExit(7)"]
    config.policy.allow_execute.append(command)
    save_config(harness_project, config)
    result = run_task_command(
        harness_project,
        config,
        task_id=task.task_id,
        argv=command,
        retries=1,
    )
    state = load_task(harness_project, task.task_id)

    assert result.exit_code == 7
    assert result.attempts == 2
    assert state.status == TaskStatus.ESCALATED
    assert result.output_path is not None
    assert result.output_path.endswith("escalation.json")


def test_budget_exhaustion_persists_structured_escalation(harness_project: Path) -> None:
    task = start_task(harness_project, "Exhaust budget")
    config = load_config(harness_project)
    config.budgets.max_retries_per_step = 0
    save_config(harness_project, config)

    with pytest.raises(BudgetExceededError, match="escalation packet"):
        run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=["true"],
            retries=1,
        )

    state = load_task(harness_project, task.task_id)
    packet_path = HarnessPaths(harness_project).task_artifacts(task.task_id) / "escalation.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert state.status == TaskStatus.ESCALATED
    assert "requested retries" in packet["reason"]
    assert packet["cost_of_waiting"]


def test_secret_bearing_arguments_are_redacted(harness_project: Path) -> None:
    task = start_task(harness_project, "Redaction")
    config = load_config(harness_project)
    command = [sys.executable, "-c", "import sys; print(sys.argv[-1])", "--token", "hunter2"]
    config.policy.allow_execute.append(command)
    save_config(harness_project, config)
    result = run_task_command(
        harness_project,
        config,
        task_id=task.task_id,
        argv=command,
    )
    assert "hunter2" not in " ".join(result.argv)
    assert result.output_path is not None
    log = (harness_project / result.output_path).read_text(encoding="utf-8")
    assert "hunter2" not in log
    assert "<redacted>" in log
    assert "hunter2" not in result.summary


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


def test_stop_activated_during_execution_cancels_remaining_retries(
    harness_project: Path,
) -> None:
    task = start_task(harness_project, "Stop retries")
    config = load_config(harness_project)
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('.hexaharness').joinpath('STOP').write_text('stop\\n'); "
        "raise SystemExit(7)",
    ]
    config.policy.allow_execute.append(command)
    save_config(harness_project, config)

    with pytest.raises(HarnessStoppedError, match="remaining command attempts"):
        run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=command,
            retries=1,
        )

    state = load_task(harness_project, task.task_id)
    assert state.attempts == 1
    assert state.status == TaskStatus.PAUSED


def test_missing_command_is_captured_as_exit_127(harness_project: Path) -> None:
    config = load_config(harness_project)
    config.policy.allow_execute.append(["definitely-not-a-real-command"])
    save_config(harness_project, config)
    task = start_task(harness_project, "Missing binary")
    result = run_task_command(
        harness_project,
        config,
        task_id=task.task_id,
        argv=["definitely-not-a-real-command"],
    )
    assert result.exit_code == 127
    assert "command not found" in result.summary


def test_human_gated_action_is_durable_and_requires_post_verification(
    harness_project: Path,
) -> None:
    config = load_config(harness_project)
    task = start_task(harness_project, "Publish release")
    inspector = harness_project / "inspect-release-state.py"
    inspector.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "from pathlib import Path",
                "state_path = Path('.hexaharness').joinpath('state', sys.argv[1] + '.json')",
                "state = json.loads(state_path.read_text(encoding='utf-8'))",
                "assert state['status'] == 'paused'",
                "assert state['next_step'].startswith('RECONCILE_EXTERNAL_ACTION:')",
                "Path('external-action-ran.txt').write_text('ran\\n', encoding='utf-8')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    command = [sys.executable, inspector.name, task.task_id]
    config.policy.ask_execute.insert(0, command)
    save_config(harness_project, config)
    pending_step = new_external_action_step(harness_project, task.task_id, command)
    stage_external_action(harness_project, task.task_id, pending_step=pending_step)

    with pytest.raises(PolicyBlockedError, match="cannot be retried"):
        run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=command,
            approved=True,
            retries=1,
        )

    result = run_task_command(
        harness_project,
        config,
        task_id=task.task_id,
        argv=command,
        approved=True,
    )
    state = load_task(harness_project, task.task_id)
    assert result.exit_code == 0
    assert (harness_project / "external-action-ran.txt").is_file()
    assert state.status == TaskStatus.ACTIVE
    assert state.next_step == format_external_action_step(
        harness_project,
        task.task_id,
        VERIFY_EXTERNAL_ACTION_RESULT,
        command,
        nonce=external_action_nonce(pending_step),
    )

    with pytest.raises(PolicyBlockedError, match="durable pending checkpoint"):
        run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=command,
            approved=True,
        )

    verification = harness_project / "external-verification.txt"
    record_write_observation(harness_project, task.task_id, verification.name)
    verification.write_text("confirmed published release\n", encoding="utf-8")
    verified = checkpoint_task(
        harness_project,
        task.task_id,
        completed_step="verified published release",
        clear_next=True,
        artifacts=[verification.name],
    )
    assert verified.next_step is None


def test_external_action_crash_leaves_reconciliation_checkpoint(
    harness_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(harness_project)
    task = start_task(harness_project, "Crash-safe publication")
    command = ["release-tool", "publish"]
    config.policy.ask_execute.insert(0, command)
    save_config(harness_project, config)
    pending_step = new_external_action_step(harness_project, task.task_id, command)
    stage_external_action(harness_project, task.task_id, pending_step=pending_step)

    def crash_before_result(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated host crash")

    monkeypatch.setattr("hexaharness.runner.execute_capture", crash_before_result)
    with pytest.raises(RuntimeError, match="simulated host crash"):
        run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=command,
            approved=True,
        )

    state = load_task(harness_project, task.task_id)
    assert state.status == TaskStatus.PAUSED
    assert state.next_step == format_external_action_step(
        harness_project,
        task.task_id,
        RECONCILE_EXTERNAL_ACTION,
        command,
        nonce=external_action_nonce(pending_step),
    )

    reconciliation = harness_project / "reconciliation.txt"
    record_write_observation(harness_project, task.task_id, reconciliation.name)
    reconciliation.write_text("external action did not complete\n", encoding="utf-8")
    with pytest.raises(PolicyBlockedError, match="post-action verification"):
        checkpoint_task(
            harness_project,
            task.task_id,
            completed_step="checked external state",
            clear_next=True,
            artifacts=[reconciliation.name],
        )
    resolved = checkpoint_task(
        harness_project,
        task.task_id,
        completed_step="confirmed external action did not complete",
        resolve_external_action=True,
        artifacts=[reconciliation.name],
    )
    assert resolved.next_step is None


def test_escalated_external_action_can_record_fresh_reconciliation_evidence(
    harness_project: Path,
) -> None:
    config = load_config(harness_project)
    task = start_task(harness_project, "Reconcile failed publication")
    command = ["release-tool", "publish"]
    config.policy.ask_execute.insert(0, command)
    save_config(harness_project, config)
    pending_step = new_external_action_step(harness_project, task.task_id, command)
    stage_external_action(harness_project, task.task_id, pending_step=pending_step)

    result = run_task_command(
        harness_project,
        config,
        task_id=task.task_id,
        argv=command,
        approved=True,
    )
    state = load_task(harness_project, task.task_id)
    assert result.exit_code == 127
    assert state.status == TaskStatus.ESCALATED
    assert state.next_step is not None
    assert state.next_step.startswith(RECONCILE_EXTERNAL_ACTION)

    evidence = harness_project / "failed-publication-reconciliation.txt"
    record_write_observation(harness_project, task.task_id, evidence.name)
    evidence.write_text("confirmed publication did not occur\n", encoding="utf-8")
    resolved = checkpoint_task(
        harness_project,
        task.task_id,
        completed_step="confirmed publication did not occur",
        resolve_external_action=True,
        artifacts=[evidence.name],
    )

    assert resolved.status == TaskStatus.ACTIVE
    assert resolved.next_step is None


def test_pending_external_action_rejects_ask_to_allow_policy_drift(
    harness_project: Path,
) -> None:
    task = start_task(harness_project, "Reject weakened publication policy")
    command = [
        "python",
        "-c",
        "from pathlib import Path; Path('policy-drift-action-ran').write_text('ran')",
    ]
    pending_step = new_external_action_step(harness_project, task.task_id, command)
    stage_external_action(harness_project, task.task_id, pending_step=pending_step)

    current = load_config(harness_project)
    current.policy.allow_execute.insert(0, command)
    save_config(harness_project, current)
    current = load_config(harness_project)

    with pytest.raises(PolicyBlockedError, match="policy changed"):
        run_task_command(
            harness_project,
            current,
            task_id=task.task_id,
            argv=command,
            approved=True,
        )

    state = load_task(harness_project, task.task_id)
    assert state.next_step == pending_step
    assert state.tool_calls == 0
    assert not (harness_project / "policy-drift-action-ran").exists()


def test_pending_external_action_preserves_ask_to_deny_policy_drift(
    harness_project: Path,
) -> None:
    task = start_task(harness_project, "Honor strengthened publication policy")
    command = [
        "python",
        "-c",
        "from pathlib import Path; Path('denied-drift-action-ran').write_text('ran')",
    ]
    pending_step = new_external_action_step(harness_project, task.task_id, command)
    stage_external_action(harness_project, task.task_id, pending_step=pending_step)

    current = load_config(harness_project)
    current.policy.deny_execute.insert(0, command)
    save_config(harness_project, current)
    current = load_config(harness_project)

    with pytest.raises(PolicyBlockedError, match="command denied"):
        run_task_command(
            harness_project,
            current,
            task_id=task.task_id,
            argv=command,
            approved=True,
        )

    state = load_task(harness_project, task.task_id)
    assert state.next_step == pending_step
    assert state.tool_calls == 0
    assert not (harness_project / "denied-drift-action-ran").exists()


def test_external_checkpoint_digest_binds_redacted_arguments(
    harness_project: Path,
) -> None:
    config = load_config(harness_project)
    task = start_task(harness_project, "Token-bound publication")
    approved_command = ["release-tool", "publish", "--token", "token-a"]
    changed_command = ["release-tool", "publish", "--token", "token-b"]
    nonce = "a" * 32
    approved_step = format_external_action_step(
        harness_project,
        task.task_id,
        PENDING_EXTERNAL_ACTION,
        approved_command,
        nonce=nonce,
    )
    changed_step = format_external_action_step(
        harness_project,
        task.task_id,
        PENDING_EXTERNAL_ACTION,
        changed_command,
        nonce=nonce,
    )
    assert "token-a" not in approved_step
    assert "token-b" not in changed_step
    assert approved_step != changed_step
    stage_external_action(
        harness_project,
        task.task_id,
        pending_step=approved_step,
    )

    with pytest.raises(PolicyBlockedError, match="does not match"):
        run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=changed_command,
            approved=True,
        )


def test_external_checkpoint_copied_to_another_task_cannot_execute(
    harness_project: Path,
) -> None:
    config = load_config(harness_project)
    command = ["release-tool", "publish"]
    config.policy.ask_execute.insert(0, command)
    save_config(harness_project, config)
    source = start_task(harness_project, "Stage source release")
    target = start_task(harness_project, "Attempt copied release")
    source_step = new_external_action_step(harness_project, source.task_id, command)

    stage_external_action(harness_project, target.task_id, pending_step=source_step)

    with pytest.raises(PolicyBlockedError, match="does not match"):
        run_task_command(
            harness_project,
            config,
            task_id=target.task_id,
            argv=command,
            approved=True,
        )
    target_state = load_task(harness_project, target.task_id)
    assert target_state.status == TaskStatus.ACTIVE
    assert target_state.next_step == source_step
