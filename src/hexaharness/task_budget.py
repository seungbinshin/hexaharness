"""Create an authorized successor while preserving the previous execution's consumption."""

from __future__ import annotations

import hashlib
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

from hexaharness.config import ensure_configuration_current, ensure_configuration_ready
from hexaharness.errors import HarnessStoppedError, PolicyBlockedError
from hexaharness.events import record_event
from hexaharness.models import BudgetConfig, HarnessConfig, TaskBudget, TaskKind, TaskState, utc_now
from hexaharness.paths import HarnessPaths
from hexaharness.state import (
    TERMINAL_STATUSES,
    configuration_lock,
    guidance_fingerprint,
    harness_lock,
    is_external_action_marker,
    list_tasks,
    load_task,
    make_task_id,
    save_task,
    task_elapsed_seconds,
    task_lock,
)


def continue_task(
    project_root: Path,
    config: HarnessConfig,
    *,
    source_task_ids: list[str],
    goal: str,
    limits: BudgetConfig,
    deadline_at: datetime,
    reason: str,
    approved: bool = False,
    unattended: bool = False,
    kind: TaskKind = TaskKind.CHANGE,
) -> TaskState:
    """Record host-confirmed authorization; this flag cannot grant native permissions."""
    if not approved:
        raise PolicyBlockedError("continuation requires explicit authorization for these limits")
    if not source_task_ids or len(set(source_task_ids)) != len(source_task_ids):
        raise PolicyBlockedError("continuation sources must be nonempty and distinct")
    # Match completion's task -> configuration -> harness locking order. Configuration
    # serialization makes concurrent continuations consume a source at most once.
    with ExitStack() as locks:
        for source_id in sorted(source_task_ids):
            locks.enter_context(task_lock(project_root, source_id))
        locks.enter_context(configuration_lock(project_root))
        locks.enter_context(harness_lock(project_root))
        ensure_configuration_current(project_root, config)
        ensure_configuration_ready(config, project_root)
        paths = HarnessPaths(project_root)
        if paths.stop_file.exists():
            raise HarnessStoppedError("emergency stop is active; continuation is blocked")
        sources = [load_task(project_root, task_id) for task_id in source_task_ids]
        lineage: set[str] = set()
        for source in sources:
            if source.status not in TERMINAL_STATUSES:
                raise PolicyBlockedError(
                    "continuation requires terminal sources; resume active work"
                )
            if is_external_action_marker(source.next_step) or source.next_step is not None:
                raise PolicyBlockedError("resolve source pending work before continuation")
            if source.improvement is not None:
                # Preserve unresolved remote calls and their reservations in their owner task.
                from hexaharness.improvement import ensure_improvement_resolved

                ensure_improvement_resolved(project_root, config, source)
            ancestry = {source.task_id}
            if source.task_budget is not None:
                ancestry.update(source.task_budget.lineage_task_ids)
            if ancestry & lineage:
                raise PolicyBlockedError("continuation sources overlap in execution ancestry")
            lineage.update(ancestry)
        for existing in list_tasks(project_root):
            if existing.task_budget is not None and set(source_task_ids).intersection(
                existing.task_budget.lineage_task_ids
            ):
                raise PolicyBlockedError("a source was already continued; continue its successor")
        contract = TaskBudget(
            limits=limits,
            deadline_at=deadline_at,
            reason=reason,
            source_task_ids=source_task_ids,
            lineage_task_ids=sorted(lineage),
            source_sha256={
                source.task_id: hashlib.sha256(
                    paths.state_file(source.task_id).read_bytes()
                ).hexdigest()
                for source in sources
            },
        )
        if contract.deadline_at <= utc_now():
            raise PolicyBlockedError("continuation deadline has already elapsed")
        state = TaskState(
            task_id=make_task_id(goal),
            goal=goal,
            kind=kind,
            unattended=unattended,
            started_at=min(source.started_at for source in sources),
            tool_calls=sum(source.tool_calls for source in sources),
            attempts=sum(source.attempts for source in sources),
            tokens_used=sum(source.tokens_used for source in sources),
            cost_usd=sum(source.cost_usd for source in sources),
            # Use no approval-time refund across overlapping historical tasks.
            approval_wait_seconds=0,
            guidance_fingerprint=guidance_fingerprint(project_root),
            task_budget=contract,
        )
        if (
            state.tool_calls >= limits.max_tool_calls
            or state.tokens_used >= limits.max_tokens
            or (
                state.cost_usd >= limits.max_cost_usd if limits.max_cost_usd else state.cost_usd > 0
            )
            or task_elapsed_seconds(state) >= limits.max_wall_time_seconds
        ):
            raise PolicyBlockedError("continuation limits leave no remaining execution budget")
        save_task(project_root, state)
        record_event(
            project_root,
            "task.continued",
            task_id=state.task_id,
            payload={
                "budget": contract.model_dump(mode="json"),
                "inherited_tool_calls": state.tool_calls,
                "inherited_tokens": state.tokens_used,
                "inherited_cost_usd": state.cost_usd,
                "native_authority_changed": False,
            },
        )
        return state
