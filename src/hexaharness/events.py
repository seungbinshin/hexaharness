from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hexaharness.io import append_jsonl
from hexaharness.models import Event
from hexaharness.paths import HarnessPaths

FAILURE_EVIDENCE_PATH_KEYS = {
    "budget.exhausted": "escalation_path",
    "command.failed": "output_path",
    "command.stopped": "output_path",
    "sensor.completed": "output_path",
    "task.escalation-created": "path",
}


def _stable_file_digest(path: Path) -> str:
    before = path.stat()
    if not path.is_file():
        raise ValueError(f"event evidence is not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
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
        raise ValueError(f"event evidence changed while it was captured: {path}")
    return digest.hexdigest()


def _bind_failure_evidence(
    project_root: Path,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Bind runtime failure events to the exact retained output bytes."""
    key = FAILURE_EVIDENCE_PATH_KEYS.get(event_type)
    raw_path = payload.get(key) if key is not None else None
    if not isinstance(raw_path, str):
        return payload
    root = project_root.resolve(strict=True)
    candidate = Path(raw_path)
    candidate = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root).as_posix()
        digest = _stable_file_digest(resolved)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return payload
    return {
        **payload,
        "evidence_path": relative,
        "evidence_sha256": digest,
    }


def record_event(
    project_root: Path,
    event_type: str,
    *,
    task_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Event:
    bound_payload = _bind_failure_evidence(project_root, event_type, dict(payload or {}))
    event = Event(event_type=event_type, task_id=task_id, payload=bound_payload)
    day = event.timestamp.astimezone(UTC).strftime("%Y-%m-%d")
    append_jsonl(HarnessPaths(project_root).events / f"{day}.jsonl", event.model_dump(mode="json"))
    return event


def iter_events(project_root: Path) -> Iterable[Event]:
    events_dir = HarnessPaths(project_root).events
    if not events_dir.is_dir():
        return
    for path in sorted(events_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield Event.model_validate(json.loads(line))


def count_events(
    project_root: Path,
    *,
    event_type: str,
    task_id: str | None = None,
    since: datetime | None = None,
) -> int:
    return sum(
        1
        for event in iter_events(project_root)
        if event.event_type == event_type
        and (task_id is None or event.task_id == task_id)
        and (since is None or event.timestamp >= since)
    )
