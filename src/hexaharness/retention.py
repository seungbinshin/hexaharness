from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from hexaharness.models import HarnessConfig, TaskStatus
from hexaharness.paths import HarnessPaths
from hexaharness.state import list_tasks

TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.STOPPED, TaskStatus.FAILED}


def plan_prune(
    project_root: Path,
    config: HarnessConfig,
    *,
    now: datetime | None = None,
) -> list[Path]:
    current = now or datetime.now(UTC)
    paths = HarnessPaths(project_root)
    state_cutoff = current - timedelta(days=config.retention.checkpoint_days)
    event_cutoff = current - timedelta(days=config.retention.event_days)
    tasks = list_tasks(project_root)
    recent_completed = {
        task.task_id
        for task in [item for item in tasks if item.status == TaskStatus.COMPLETED][
            : config.retention.keep_latest_completed_tasks
        ]
    }
    candidates: set[Path] = set()

    for task in tasks:
        if (
            task.status in TERMINAL_STATUSES
            and task.updated_at < state_cutoff
            and task.task_id not in recent_completed
        ):
            candidates.add(paths.state_file(task.task_id))
            artifact_dir = paths.task_artifacts(task.task_id)
            if artifact_dir.is_dir():
                candidates.update(path for path in artifact_dir.rglob("*") if path.is_file())

    if paths.events.is_dir():
        for event_file in paths.events.glob("*.jsonl"):
            modified = datetime.fromtimestamp(event_file.stat().st_mtime, tz=UTC)
            if modified < event_cutoff:
                candidates.add(event_file)

    if paths.proposals.is_dir():
        for proposal in paths.proposals.glob("*.json"):
            modified = datetime.fromtimestamp(proposal.stat().st_mtime, tz=UTC)
            if modified < state_cutoff:
                candidates.add(proposal)

    return sorted(candidates)


def apply_prune(project_root: Path, candidates: list[Path]) -> list[str]:
    harness_root = HarnessPaths(project_root).harness_dir.resolve()
    deleted: list[str] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(harness_root) or resolved == harness_root:
            raise ValueError(f"refusing to prune outside the harness runtime: {candidate}")
        if resolved.is_file() and not resolved.is_symlink():
            relative = resolved.relative_to(project_root.resolve()).as_posix()
            resolved.unlink()
            deleted.append(relative)
            parent = resolved.parent
            while parent != harness_root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    return deleted
