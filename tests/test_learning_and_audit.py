from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hexaharness import learning as learning_module
from hexaharness.audit import audit_harness, audit_summary
from hexaharness.cli import app
from hexaharness.config import load_config, save_config
from hexaharness.events import iter_events
from hexaharness.learning import load_guide_rules, record_learning
from hexaharness.models import AuditStatus, FailureClass, HarnessLayer, TaskStatus
from hexaharness.paths import HarnessPaths
from hexaharness.runner import run_task_command
from hexaharness.state import checkpoint_task, set_task_status, start_task

runner = CliRunner()


def _record_rule_concurrently(
    project_root: str,
    task_id: str,
    evidence_path: str,
    index: int,
    start_gate: Any,
) -> None:
    start_gate.wait()
    record_learning(
        Path(project_root),
        task_id=task_id,
        evidence_paths=[evidence_path],
        failure_class=FailureClass.MISSING_CONTEXT,
        failure_summary=f"Concurrent failure {index}",
        proposed_fix=f"Concurrent fix {index}",
        verification=f"Concurrent verification {index}",
        target_layer=HarnessLayer.GUIDE,
        guide_rule=f"Concurrent rule {index}.",
    )


def _task_with_failure_evidence(
    project_root: Path, goal: str, evidence_name: str
) -> tuple[str, str]:
    failing_script = project_root / f"{Path(evidence_name).stem}-command.py"
    failing_script.write_text("raise SystemExit(7)\n", encoding="utf-8")
    task = start_task(project_root, goal)
    command = [sys.executable, failing_script.name]
    config = load_config(project_root)
    config.policy.allow_execute.append(command)
    save_config(project_root, config)
    result = run_task_command(
        project_root,
        task_id=task.task_id,
        config=config,
        argv=command,
    )
    assert result.exit_code == 7
    failure = next(
        event
        for event in reversed(list(iter_events(project_root)))
        if event.event_type == "command.failed" and event.task_id == task.task_id
    )
    return task.task_id, str(failure.payload["output_path"])


def test_learning_renders_traceable_guide_rule(harness_project: Path) -> None:
    task_id, evidence = _task_with_failure_evidence(
        harness_project, "Observed missing context", "failure.txt"
    )
    record = record_learning(
        harness_project,
        failure_class=FailureClass.MISSING_CONTEXT,
        failure_summary="Agent guessed the build command",
        proposed_fix="Record the exact command",
        verification="Fresh session runs the documented command",
        task_id=task_id,
        evidence_paths=[evidence],
        target_layer=HarnessLayer.GUIDE,
        guide_rule="Run the configured build command before reporting completion.",
    )

    rules = load_guide_rules(harness_project)
    rendered = HarnessPaths(harness_project).guides_markdown.read_text(encoding="utf-8")
    assert record.guide_rule_id == rules[0].rule_id
    assert rules[0].task_id == task_id
    assert rules[0].evidence_paths == [evidence]
    assert rules[0].evidence_digests[evidence]
    assert rules[0].source_failure_events == ["command.failed"]
    assert "Agent guessed the build command" in rendered
    assert "Run the configured build command" in rendered


def test_learning_rejects_guide_rule_for_stronger_layer(harness_project: Path) -> None:
    task_id, evidence = _task_with_failure_evidence(
        harness_project, "Observed formatting failure", "lint-failure.txt"
    )
    with pytest.raises(ValueError, match="target layer is `guide`"):
        record_learning(
            harness_project,
            failure_class=FailureClass.KNOWN_BAD_PATTERN,
            failure_summary="Bad format escaped review",
            proposed_fix="Add a linter rule",
            verification="Adversarial fixture fails lint",
            task_id=task_id,
            evidence_paths=[evidence],
            target_layer=HarnessLayer.SENSOR,
            guide_rule="Do not use the bad format.",
        )


def test_learning_requires_a_source_task_and_evidence(harness_project: Path) -> None:
    with pytest.raises(ValueError, match="task ID"):
        record_learning(
            harness_project,
            failure_class=FailureClass.MISSING_CONTEXT,
            failure_summary="Missing context",
            proposed_fix="Record it",
            verification="Rerun",
        )

    task = start_task(harness_project, "No retained evidence")
    with pytest.raises(ValueError, match="evidence artifact"):
        record_learning(
            harness_project,
            task_id=task.task_id,
            failure_class=FailureClass.MISSING_CONTEXT,
            failure_summary="Missing context",
            proposed_fix="Record it",
            verification="Rerun",
        )


def test_manual_pause_is_not_accepted_as_failure_provenance(harness_project: Path) -> None:
    task = start_task(harness_project, "Paused without a failure")
    evidence = harness_project / "manual-pause.txt"
    evidence.write_text("ordinary progress\n", encoding="utf-8")
    checkpoint_task(harness_project, task.task_id, artifacts=[evidence.name])
    set_task_status(harness_project, task.task_id, TaskStatus.PAUSED)

    with pytest.raises(ValueError, match="no recorded failure"):
        record_learning(
            harness_project,
            task_id=task.task_id,
            evidence_paths=[evidence.name],
            failure_class=FailureClass.UNKNOWN,
            failure_summary="Manual pause",
            proposed_fix="Do not treat status as failure evidence",
            verification="Require a direct failure event",
        )

    evidence = harness_project / "unproven.txt"
    evidence.write_text("claim without failure\n", encoding="utf-8")
    checkpoint_task(harness_project, task.task_id, artifacts=[evidence.name])
    with pytest.raises(ValueError, match="no recorded failure"):
        record_learning(
            harness_project,
            task_id=task.task_id,
            evidence_paths=[evidence.name],
            failure_class=FailureClass.MISSING_CONTEXT,
            failure_summary="Unobserved claim",
            proposed_fix="Do not accept it",
            verification="Rerun",
        )


def test_audit_does_not_count_five_rules_from_one_failure_task(
    harness_project: Path,
) -> None:
    task_id, evidence = _task_with_failure_evidence(
        harness_project, "Single observed failure", "single-failure.txt"
    )
    for index in range(5):
        record_learning(
            harness_project,
            task_id=task_id,
            evidence_paths=[evidence],
            failure_class=FailureClass.MISSING_CONTEXT,
            failure_summary=f"Failure claim {index}",
            proposed_fix=f"Fix {index}",
            verification=f"Verify {index}",
            target_layer=HarnessLayer.GUIDE,
            guide_rule=f"Apply distinct control {index}.",
        )

    rule_check = audit_harness(harness_project)[1]

    assert rule_check.status == AuditStatus.WARN
    assert "5 provenance-verified" in rule_check.evidence
    assert "1 distinct failure tasks" in rule_check.evidence


def test_dry_run_policy_denials_cannot_fake_five_failure_rules(
    harness_project: Path,
) -> None:
    evidence = harness_project / "preexisting-readme.md"
    evidence.write_text("not a failure output\n", encoding="utf-8")
    config = load_config(harness_project)
    config.policy.deny_write_paths.append(evidence.name)
    save_config(harness_project, config)

    for index in range(5):
        task = start_task(harness_project, f"Dry-run denial {index}")
        denied = runner.invoke(
            app,
            [
                "policy-check",
                "--root",
                str(harness_project),
                "--task-id",
                task.task_id,
                "--path",
                evidence.name,
                "--write",
            ],
        )
        assert denied.exit_code == 2
        with pytest.raises(ValueError, match="no recorded failure"):
            record_learning(
                harness_project,
                task_id=task.task_id,
                evidence_paths=[evidence.name],
                failure_class=FailureClass.MISSING_CONTEXT,
                failure_summary=f"Fabricated claim {index}",
                proposed_fix=f"Fabricated fix {index}",
                verification=f"Fabricated verification {index}",
                target_layer=HarnessLayer.GUIDE,
                guide_rule=f"Fabricated rule {index}.",
            )

    rule_check = audit_harness(harness_project)[1]
    assert rule_check.status == AuditStatus.WARN
    assert rule_check.evidence.startswith("0 provenance-verified active rules")


def test_learning_rejects_replaced_or_racing_failure_output(
    harness_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id, evidence = _task_with_failure_evidence(
        harness_project,
        "Replace retained output",
        "replace-output.txt",
    )
    evidence_path = harness_project / evidence
    evidence_path.write_text("forged retained output\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no longer matches"):
        record_learning(
            harness_project,
            task_id=task_id,
            evidence_paths=[evidence],
            failure_class=FailureClass.MISSING_CONTEXT,
            failure_summary="Replaced output",
            proposed_fix="Reject replacement",
            verification="Digest must match the failure event",
        )

    task_id, evidence = _task_with_failure_evidence(
        harness_project,
        "Race retained output",
        "race-output.txt",
    )
    evidence_path = harness_project / evidence
    original_sha256 = learning_module._sha256_file

    def replace_during_hash(path: Path) -> str:
        digest = original_sha256(path)
        path.write_bytes(path.read_bytes() + b"changed during capture\n")
        return digest

    monkeypatch.setattr(learning_module, "_sha256_file", replace_during_hash)
    with pytest.raises(ValueError, match="changed while it was captured"):
        record_learning(
            harness_project,
            task_id=task_id,
            evidence_paths=[evidence],
            failure_class=FailureClass.MISSING_CONTEXT,
            failure_summary="Racing output",
            proposed_fix="Reject the race",
            verification="Identity must remain stable",
        )


def test_concurrent_learning_keeps_yaml_markdown_and_decisions_consistent(
    harness_project: Path,
) -> None:
    sources = [
        _task_with_failure_evidence(
            harness_project,
            f"Concurrent source {index}",
            f"concurrent-{index}.txt",
        )
        for index in range(4)
    ]
    context = multiprocessing.get_context("spawn")
    start_gate = context.Event()
    processes = [
        context.Process(
            target=_record_rule_concurrently,
            args=(str(harness_project), task_id, evidence, index, start_gate),
        )
        for index, (task_id, evidence) in enumerate(sources)
    ]
    for process in processes:
        process.start()
    start_gate.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    rules = load_guide_rules(harness_project)
    assert {rule.rule for rule in rules} == {f"Concurrent rule {index}." for index in range(4)}
    rendered = HarnessPaths(harness_project).guides_markdown.read_text(encoding="utf-8")
    assert all(f"Concurrent rule {index}." in rendered for index in range(4))
    decisions = [
        json.loads(line)
        for line in HarnessPaths(harness_project).decisions.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(decisions) == 4


def test_audit_distinguishes_structural_failures_from_evidence_gaps(
    harness_project: Path,
) -> None:
    checks = audit_harness(harness_project)
    summary = audit_summary(checks)

    assert len(checks) == 12
    assert summary[AuditStatus.FAIL.value] == 0
    assert summary[AuditStatus.WARN.value] == 9
    assert {check.number for check in checks if check.status == AuditStatus.WARN} == {
        2,
        3,
        4,
        5,
        6,
        8,
        9,
        11,
        12,
    }


@pytest.mark.parametrize("contents", ["", "unrelated instructions\n"], ids=["empty", "unrelated"])
def test_audit_rejects_agent_guides_without_managed_commands(
    harness_project: Path, contents: str
) -> None:
    (harness_project / "AGENTS.md").write_text(contents, encoding="utf-8")

    guide_check = audit_harness(harness_project)[0]

    assert guide_check.status == AuditStatus.FAIL
    assert "missing-or-stale" in guide_check.evidence


@pytest.mark.parametrize("field", ["build", "test", "lint", "typecheck"])
def test_audit_rejects_stale_managed_command_guidance(harness_project: Path, field: str) -> None:
    config = load_config(harness_project)
    setattr(config.project, field, [f"updated-{field}"])
    save_config(harness_project, config)

    guide_check = audit_harness(harness_project)[0]

    assert guide_check.status == AuditStatus.FAIL
    assert "managed-commands=missing-or-stale" in guide_check.evidence


def test_audit_command_guidance_is_not_coupled_to_managed_prose(
    harness_project: Path,
) -> None:
    agents_path = harness_project / "AGENTS.md"
    content = agents_path.read_text(encoding="utf-8").replace(
        "Use the HexaHarness skill automatically",
        "Use the HexaHarness lifecycle skill automatically",
    )
    agents_path.write_text(content, encoding="utf-8")

    guide_check = audit_harness(harness_project)[0]

    assert guide_check.status == AuditStatus.PASS
    assert "managed-commands=current" in guide_check.evidence
