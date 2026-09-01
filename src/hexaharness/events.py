from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hexaharness.io import append_jsonl
from hexaharness.models import Event
from hexaharness.paths import HarnessPaths


def record_event(
    project_root: Path,
    event_type: str,
    *,
    task_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Event:
    event = Event(event_type=event_type, task_id=task_id, payload=payload or {})
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
