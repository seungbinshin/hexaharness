from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hexaharness.config import load_config
from hexaharness.io import write_json
from hexaharness.models import TaskStatus
from hexaharness.paths import HarnessPaths
from hexaharness.retention import apply_prune, plan_prune
from hexaharness.state import start_task


def test_prune_removes_expired_terminal_evidence_but_keeps_latest_completed(
    harness_project: Path,
) -> None:
    now = datetime(2026, 1, 31, tzinfo=UTC)
    old = now - timedelta(days=100)
    paths = HarnessPaths(harness_project)
    config = load_config(harness_project)
    config.retention.checkpoint_days = 30
    config.retention.event_days = 60
    config.retention.keep_latest_completed_tasks = 1

    stopped = start_task(harness_project, "expired stopped task")
    stopped.status = TaskStatus.STOPPED
    stopped.updated_at = old
    stopped.ended_at = old
    write_json(paths.state_file(stopped.task_id), stopped.model_dump(mode="json"))
    artifact = paths.task_artifacts(stopped.task_id) / "command.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("expired\n", encoding="utf-8")

    completed = start_task(harness_project, "protected completed task")
    completed.status = TaskStatus.COMPLETED
    completed.updated_at = old + timedelta(days=1)
    completed.ended_at = completed.updated_at
    write_json(paths.state_file(completed.task_id), completed.model_dump(mode="json"))

    event = paths.events / "expired.jsonl"
    event.write_text("{}\n", encoding="utf-8")
    proposal = paths.proposals / "expired.json"
    proposal.write_text("{}\n", encoding="utf-8")
    os.utime(event, (old.timestamp(), old.timestamp()))
    os.utime(proposal, (old.timestamp(), old.timestamp()))

    candidates = plan_prune(harness_project, config, now=now)

    assert paths.state_file(stopped.task_id) in candidates
    assert artifact in candidates
    assert event in candidates
    assert proposal in candidates
    assert paths.state_file(completed.task_id) not in candidates

    deleted = apply_prune(harness_project, candidates)
    assert len(deleted) == 4
    assert paths.state_file(completed.task_id).is_file()
    assert all(not path.exists() for path in candidates)


def test_prune_refuses_paths_outside_runtime(harness_project: Path) -> None:
    outside = harness_project / "pyproject.toml"

    with pytest.raises(ValueError, match="outside the harness runtime"):
        apply_prune(harness_project, [outside])
