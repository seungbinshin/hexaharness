from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hexaharness.events import iter_events, record_event
from hexaharness.models import HarnessConfig, TaskState


def repeated_error_count(project_root: Path, task_id: str, fingerprint: str) -> int:
    return sum(
        1
        for event in iter_events(project_root)
        if event.task_id == task_id
        and event.event_type == "command.failed"
        and event.payload.get("fingerprint") == fingerprint
    )


def active_trip_wires(
    project_root: Path,
    config: HarnessConfig,
    state: TaskState,
    *,
    error_fingerprint: str | None = None,
) -> list[str]:
    active: list[str] = []
    elapsed = (datetime.now(UTC) - state.started_at).total_seconds()
    if state.tool_calls >= config.budgets.max_tool_calls:
        active.append("tool-budget")
    if state.cost_usd >= config.budgets.max_cost_usd and config.budgets.max_cost_usd > 0:
        active.append("cost-budget")
    if state.tokens_used >= config.budgets.max_tokens:
        active.append("token-budget")
    if elapsed >= config.budgets.max_wall_time_seconds:
        active.append("wall-time-budget")
    if error_fingerprint:
        threshold = next(
            (
                int(item.threshold)
                for item in config.trip_wires
                if item.metric == "same_error_count"
            ),
            3,
        )
        if repeated_error_count(project_root, state.task_id, error_fingerprint) >= threshold:
            active.append("repeated-error")
    for name in active:
        record_event(
            project_root,
            "trip-wire.fired",
            task_id=state.task_id,
            payload={"name": name},
        )
    return active
