from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path

import yaml

from hexaharness.io import append_jsonl, atomic_write_text, write_json
from hexaharness.models import (
    FailureClass,
    GuideRule,
    HarnessLayer,
    LearningRecord,
)
from hexaharness.paths import HarnessPaths
from hexaharness.scaffold import render_guides

PREFERRED_LAYER: dict[FailureClass, HarnessLayer] = {
    FailureClass.KNOWN_BAD_PATTERN: HarnessLayer.SENSOR,
    FailureClass.MISSING_CONTEXT: HarnessLayer.GUIDE,
    FailureClass.WRONG_TOOL: HarnessLayer.PERMISSION,
    FailureClass.QUALITY_DRIFT: HarnessLayer.SENSOR,
    FailureClass.STATE_LOSS: HarnessLayer.MEMORY,
    FailureClass.UNSAFE_ACTION: HarnessLayer.PERMISSION,
    FailureClass.COST_OVERRUN: HarnessLayer.OBSERVABILITY,
    FailureClass.UNKNOWN: HarnessLayer.GUIDE,
}


def _new_id(prefix: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ").lower()
    return f"{prefix}-{timestamp}-{secrets.token_hex(2)}"


def _load_rule_document(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"schema_version": 1, "rules": []}
    with path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream) or {}
    rules = raw.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("guide-rules.yaml must contain a rules list")
    return {"schema_version": raw.get("schema_version", 1), "rules": rules}


def record_learning(
    project_root: Path,
    *,
    failure_class: FailureClass,
    failure_summary: str,
    proposed_fix: str,
    verification: str,
    task_id: str | None = None,
    target_layer: HarnessLayer | None = None,
    guide_rule: str | None = None,
) -> LearningRecord:
    paths = HarnessPaths(project_root)
    layer = target_layer or PREFERRED_LAYER[failure_class]
    guide_rule_id: str | None = None
    if guide_rule and layer != HarnessLayer.GUIDE:
        raise ValueError(
            "a guide rule can only be applied when the selected target layer is `guide`; "
            "record sensor or policy fixes as proposals and implement them deterministically"
        )

    if guide_rule:
        document = _load_rule_document(paths.guide_rules)
        raw_rules = document["rules"]
        assert isinstance(raw_rules, list)
        existing = [GuideRule.model_validate(item) for item in raw_rules]
        if any(item.active and item.rule.strip() == guide_rule.strip() for item in existing):
            raise ValueError("an identical active guide rule already exists")
        guide_rule_id = _new_id("guide")
        rule = GuideRule(
            rule_id=guide_rule_id,
            failure_class=failure_class,
            failure_summary=failure_summary,
            rule=guide_rule,
            verification=verification,
        )
        existing.append(rule)
        serialized_rules = [item.model_dump(mode="json") for item in existing]
        document["rules"] = serialized_rules
        atomic_write_text(
            paths.guide_rules,
            yaml.safe_dump(document, sort_keys=False, allow_unicode=False, width=100),
        )
        atomic_write_text(paths.guides_markdown, render_guides(serialized_rules))

    record = LearningRecord(
        learning_id=_new_id("learning"),
        task_id=task_id,
        failure_class=failure_class,
        failure_summary=failure_summary,
        target_layer=layer,
        proposed_fix=proposed_fix,
        verification=verification,
        guide_rule_id=guide_rule_id,
    )
    append_jsonl(paths.decisions, record.model_dump(mode="json"))
    if layer != HarnessLayer.GUIDE:
        write_json(paths.proposals / f"{record.learning_id}.json", record.model_dump(mode="json"))
    return record


def load_guide_rules(project_root: Path) -> list[GuideRule]:
    document = _load_rule_document(HarnessPaths(project_root).guide_rules)
    raw_rules = document["rules"]
    assert isinstance(raw_rules, list)
    return [GuideRule.model_validate(item) for item in raw_rules]
