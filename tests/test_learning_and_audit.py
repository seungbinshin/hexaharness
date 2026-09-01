from __future__ import annotations

from pathlib import Path

import pytest

from hexaharness.audit import audit_harness, audit_summary
from hexaharness.learning import load_guide_rules, record_learning
from hexaharness.models import AuditStatus, FailureClass, HarnessLayer
from hexaharness.paths import HarnessPaths


def test_learning_renders_traceable_guide_rule(harness_project: Path) -> None:
    record = record_learning(
        harness_project,
        failure_class=FailureClass.MISSING_CONTEXT,
        failure_summary="Agent guessed the build command",
        proposed_fix="Record the exact command",
        verification="Fresh session runs the documented command",
        target_layer=HarnessLayer.GUIDE,
        guide_rule="Run the configured build command before reporting completion.",
    )

    rules = load_guide_rules(harness_project)
    rendered = HarnessPaths(harness_project).guides_markdown.read_text(encoding="utf-8")
    assert record.guide_rule_id == rules[0].rule_id
    assert "Agent guessed the build command" in rendered
    assert "Run the configured build command" in rendered


def test_learning_rejects_guide_rule_for_stronger_layer(harness_project: Path) -> None:
    with pytest.raises(ValueError, match="target layer is `guide`"):
        record_learning(
            harness_project,
            failure_class=FailureClass.KNOWN_BAD_PATTERN,
            failure_summary="Bad format escaped review",
            proposed_fix="Add a linter rule",
            verification="Adversarial fixture fails lint",
            target_layer=HarnessLayer.SENSOR,
            guide_rule="Do not use the bad format.",
        )


def test_audit_distinguishes_structural_failures_from_evidence_gaps(
    harness_project: Path,
) -> None:
    checks = audit_harness(harness_project)
    summary = audit_summary(checks)

    assert len(checks) == 12
    assert summary[AuditStatus.FAIL.value] == 0
    assert summary[AuditStatus.WARN.value] == 2
    assert {check.number for check in checks if check.status == AuditStatus.WARN} == {2, 12}
