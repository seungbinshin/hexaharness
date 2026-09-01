from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path

import yaml

from hexaharness.events import iter_events
from hexaharness.io import append_jsonl, atomic_write_text, write_json
from hexaharness.models import (
    Event,
    FailureClass,
    GuideRule,
    HarnessLayer,
    LearningRecord,
    TaskState,
)
from hexaharness.paths import HarnessPaths
from hexaharness.scaffold import render_guides
from hexaharness.state import (
    _sha256_file,
    harness_lock,
    load_task,
    validate_artifacts,
)

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

FAILURE_EVENT_TYPES = frozenset(
    {
        "budget.exhausted",
        "command.failed",
        "command.stopped",
        "sensor.blocked",
        "task.escalation-created",
        "trip-wire.fired",
    }
)


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


def _is_failure_event(event: Event) -> bool:
    if event.event_type == "sensor.completed":
        return not bool(event.payload.get("passed"))
    return event.event_type in FAILURE_EVENT_TYPES


def _event_evidence(event: Event) -> dict[str, str]:
    path = event.payload.get("evidence_path")
    digest = event.payload.get("evidence_sha256")
    if (
        not isinstance(path, str)
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        return {}
    return {path: digest}


def _stable_evidence_digest(path: Path) -> str:
    """Hash a retained output while rejecting identity or metadata changes."""
    before = path.stat()
    if not path.is_file():
        raise ValueError(f"learning evidence must be a regular file: {path}")
    digest = _sha256_file(path)
    after = path.stat()
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity:
        raise ValueError(f"learning evidence changed while it was captured: {path}")
    return digest


def _task_failure_events(
    project_root: Path,
    task: TaskState,
    *,
    before: datetime | None = None,
) -> list[Event]:
    return [
        event
        for event in iter_events(project_root)
        if event.task_id == task.task_id
        and event.timestamp >= task.started_at
        and (before is None or event.timestamp <= before)
        and _is_failure_event(event)
    ]


def learning_provenance_is_current(
    project_root: Path,
    claim: GuideRule | LearningRecord,
    task: TaskState,
) -> bool:
    """Revalidate a persisted learning claim without trusting its stored paths alone."""
    if claim.task_id != task.task_id or not claim.evidence_paths or not claim.evidence_digests:
        return False
    if set(claim.evidence_paths) != set(claim.evidence_digests):
        return False
    failure_events = _task_failure_events(project_root, task, before=claim.created_at)
    if not failure_events or not claim.source_failure_events:
        return False
    claimed_types = set(claim.source_failure_events)
    contributing_events = [
        event
        for event in failure_events
        if event.event_type in claimed_types
        and any(
            _event_evidence(event).get(path) == claim.evidence_digests[path]
            for path in claim.evidence_paths
        )
    ]
    if {event.event_type for event in contributing_events} != claimed_types:
        return False
    for evidence_path in claim.evidence_paths:
        if not any(
            _event_evidence(event).get(evidence_path) == claim.evidence_digests[evidence_path]
            for event in contributing_events
        ):
            return False
        path = project_root / evidence_path
        try:
            current = (
                path.is_file()
                and _stable_evidence_digest(path) == claim.evidence_digests[evidence_path]
            )
        except (OSError, ValueError):
            return False
        if not current:
            return False
    return True


def _derive_learning_provenance(
    project_root: Path,
    task: TaskState,
    evidence_paths: list[str],
) -> tuple[dict[str, str], list[str]]:
    failure_events = _task_failure_events(project_root, task)
    if not failure_events:
        raise ValueError("source task has no recorded failure event")
    evidence_set = set(evidence_paths)
    contributing_events = [
        event for event in failure_events if set(_event_evidence(event)) & evidence_set
    ]
    origins = {path for event in contributing_events for path in _event_evidence(event)}
    unproven = [path for path in evidence_paths if path not in origins]
    if unproven:
        raise ValueError(
            "learning evidence must originate from the source task: " + ", ".join(unproven)
        )
    digests: dict[str, str] = {}
    for evidence_path in evidence_paths:
        path = project_root / evidence_path
        if not path.is_file():
            raise ValueError(f"learning evidence must be a regular file: {evidence_path}")
        digest = _stable_evidence_digest(path)
        event_digests = {
            evidence[evidence_path]
            for event in contributing_events
            if evidence_path in (evidence := _event_evidence(event))
        }
        if digest not in event_digests:
            raise ValueError(
                f"learning evidence no longer matches the retained failure output: {evidence_path}"
            )
        digests[evidence_path] = digest
    matching_events = [
        event
        for event in contributing_events
        if any(_event_evidence(event).get(path) == digest for path, digest in digests.items())
    ]
    return digests, sorted({event.event_type for event in matching_events})


def _record_learning_locked(
    project_root: Path,
    *,
    failure_class: FailureClass,
    failure_summary: str,
    proposed_fix: str,
    verification: str,
    task_id: str,
    evidence_paths: list[str] | None = None,
    target_layer: HarnessLayer | None = None,
    guide_rule: str | None = None,
) -> LearningRecord:
    paths = HarnessPaths(project_root)
    task = load_task(project_root, task_id)
    normalized_evidence = validate_artifacts(project_root, evidence_paths)
    if not normalized_evidence:
        raise ValueError("learning records require at least one existing evidence artifact")
    evidence_digests, source_failure_events = _derive_learning_provenance(
        project_root,
        task,
        normalized_evidence,
    )
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
            task_id=task_id,
            evidence_paths=normalized_evidence,
            evidence_digests=evidence_digests,
            source_failure_events=source_failure_events,
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
        evidence_paths=normalized_evidence,
        evidence_digests=evidence_digests,
        source_failure_events=source_failure_events,
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


def record_learning(
    project_root: Path,
    *,
    failure_class: FailureClass,
    failure_summary: str,
    proposed_fix: str,
    verification: str,
    task_id: str | None = None,
    evidence_paths: list[str] | None = None,
    target_layer: HarnessLayer | None = None,
    guide_rule: str | None = None,
) -> LearningRecord:
    """Serialize provenance validation and all learning outputs as one project write."""
    if task_id is None:
        raise ValueError("learning records require the task ID that observed the failure")
    with harness_lock(project_root):
        return _record_learning_locked(
            project_root,
            failure_class=failure_class,
            failure_summary=failure_summary,
            proposed_fix=proposed_fix,
            verification=verification,
            task_id=task_id,
            evidence_paths=evidence_paths,
            target_layer=target_layer,
            guide_rule=guide_rule,
        )


def load_guide_rules(project_root: Path) -> list[GuideRule]:
    document = _load_rule_document(HarnessPaths(project_root).guide_rules)
    raw_rules = document["rules"]
    assert isinstance(raw_rules, list)
    return [GuideRule.model_validate(item) for item in raw_rules]
