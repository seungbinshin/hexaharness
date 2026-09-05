from __future__ import annotations

from pathlib import Path

from hexaharness.events import iter_events, record_event
from hexaharness.models import HarnessConfig, TaskState
from hexaharness.state import task_elapsed_seconds


def repeated_error_count(project_root: Path, task_id: str, fingerprint: str) -> int:
    count = 0
    for event in iter_events(project_root):
        if event.task_id != task_id:
            continue
        if event.event_type == "command.succeeded":
            count = 0
        elif event.event_type == "command.failed":
            count = count + 1 if event.payload.get("fingerprint") == fingerprint else 0
    return count


def active_trip_wires(
    project_root: Path,
    config: HarnessConfig,
    state: TaskState,
    *,
    error_fingerprint: str | None = None,
    required_sensor_failures: int = 0,
) -> list[str]:
    active: dict[str, str] = {}
    elapsed = task_elapsed_seconds(state)
    if state.tool_calls >= config.budgets.max_tool_calls:
        active["tool-budget"] = "stop-and-preserve-state"
    cost_limit_reached = (
        state.cost_usd > 0
        if config.budgets.max_cost_usd == 0
        else state.cost_usd >= config.budgets.max_cost_usd
    )
    if cost_limit_reached:
        active["cost-budget"] = "pause-and-inspect"
    if state.tokens_used >= config.budgets.max_tokens:
        active["token-budget"] = "stop-and-preserve-state"
    if elapsed >= config.budgets.max_wall_time_seconds:
        active["wall-time-budget"] = "stop-and-preserve-state"

    for specification in config.trip_wires:
        observed: float | None = None
        if specification.metric == "tool_calls":
            observed = float(state.tool_calls)
        elif specification.metric == "cost_usd":
            observed = state.cost_usd
        elif specification.metric == "tokens_used":
            observed = float(state.tokens_used)
        elif specification.metric == "wall_time_seconds":
            observed = elapsed
        elif specification.metric == "same_error_count" and error_fingerprint:
            observed = float(repeated_error_count(project_root, state.task_id, error_fingerprint))
        elif specification.metric == "required_sensor_failure":
            observed = float(required_sensor_failures)
        if observed is not None and observed >= specification.threshold:
            active.setdefault(specification.name, specification.response)

    for name, response in active.items():
        record_event(
            project_root,
            "trip-wire.fired",
            task_id=state.task_id,
            payload={"name": name, "response": response},
        )
    return list(active)
