from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hexaharness.cli import app
from hexaharness.config import load_config
from hexaharness.errors import BudgetExceededError, HarnessStoppedError, PolicyBlockedError
from hexaharness.models import BudgetConfig, TaskStatus, TripWireSpec
from hexaharness.paths import HarnessPaths
from hexaharness.runner import execution_time_remaining, run_task_command
from hexaharness.state import load_task, save_task, set_task_status, start_task
from hexaharness.task_budget import continue_task
from hexaharness.tripwires import active_trip_wires


def stopped(root: Path, *, tools: int = 4) -> str:
    state = start_task(root, "original bounded work")
    state.tool_calls = tools
    state.attempts = tools
    state.tokens_used = 120
    state.cost_usd = 0.1
    save_task(root, state)
    set_task_status(root, state.task_id, TaskStatus.STOPPED, error="reviewed budget exhaustion")
    return state.task_id


def continuation(root: Path, source: str, **overrides: object):
    options = {
        "source_task_ids": [source],
        "goal": "authorized continuation",
        "limits": BudgetConfig(
            max_wall_time_seconds=43_200, max_tool_calls=100, max_tokens=1_000_000
        ),
        "deadline_at": datetime.now(UTC) + timedelta(hours=2),
        "reason": "user authorized this work only",
        "approved": True,
    }
    options.update(overrides)
    return continue_task(root, load_config(root), **options)


def test_continuation_inherits_usage_and_keeps_source_and_config_bytes(harness_project: Path):
    root = harness_project
    source = stopped(root)
    source_file = HarnessPaths(root).state_file(source)
    original = source_file.read_bytes()
    config_bytes = HarnessPaths(root).config.read_bytes()
    result = continuation(root, source)
    previous = load_task(root, source)
    assert result.status == TaskStatus.ACTIVE
    assert result.tool_calls == result.attempts == 4
    assert result.tokens_used == 120 and result.cost_usd == 0.1
    assert result.started_at == previous.started_at
    assert result.task_budget.source_task_ids == [source]
    assert result.external_access == [] and result.external_action_nonces == []
    assert result.sensor_results == [] and result.artifact_evidence == []
    assert source_file.read_bytes() == original
    assert HarnessPaths(root).config.read_bytes() == config_bytes


def test_authorization_is_required_before_creating_any_checkpoint(harness_project: Path):
    source = stopped(harness_project)
    before = set(HarnessPaths(harness_project).states.glob("*.json"))
    with pytest.raises(PolicyBlockedError, match="authorization"):
        continuation(harness_project, source, approved=False)
    assert set(HarnessPaths(harness_project).states.glob("*.json")) == before


@pytest.mark.parametrize(
    "source_status", [TaskStatus.ACTIVE, TaskStatus.PAUSED, TaskStatus.ESCALATED]
)
def test_nonterminal_source_cannot_fork_its_remaining_budget(harness_project: Path, source_status):
    source = start_task(harness_project, "unfinished work")
    if source_status != TaskStatus.ACTIVE:
        set_task_status(harness_project, source.task_id, source_status)
    with pytest.raises(PolicyBlockedError, match="terminal"):
        continuation(harness_project, source.task_id)


def test_source_can_be_consumed_once_but_successor_can_continue(harness_project: Path):
    source = stopped(harness_project)
    successor = continuation(harness_project, source)
    with pytest.raises(PolicyBlockedError, match="already"):
        continuation(harness_project, source)
    set_task_status(harness_project, successor.task_id, TaskStatus.STOPPED)
    next_state = continuation(harness_project, successor.task_id)
    assert next_state.tool_calls == 4
    with pytest.raises(PolicyBlockedError):
        continuation(harness_project, source, source_task_ids=[source, successor.task_id])


def test_multiple_terminal_sources_are_summed_without_new_budget(harness_project: Path):
    first, second = stopped(harness_project, tools=7), stopped(harness_project, tools=8)
    state = continuation(harness_project, first, source_task_ids=[first, second])
    assert state.tool_calls == 15 and state.tokens_used == 240
    assert state.cost_usd == pytest.approx(0.2)


def test_expired_deadline_and_insufficient_budget_are_rejected(harness_project: Path):
    source = stopped(harness_project)
    with pytest.raises(PolicyBlockedError, match="deadline"):
        continuation(harness_project, source, deadline_at=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(PolicyBlockedError, match="remaining"):
        continuation(harness_project, source, limits=BudgetConfig(max_tool_calls=4))


def test_override_is_immutable_through_generic_state_save(harness_project: Path):
    state = continuation(harness_project, stopped(harness_project))
    state.task_budget.limits.max_tokens += 100
    with pytest.raises(PolicyBlockedError, match="budget"):
        save_task(harness_project, state)


def test_absolute_deadline_bounds_execution_even_during_approval_wait(harness_project: Path):
    state = continuation(
        harness_project,
        stopped(harness_project),
        deadline_at=datetime.now(UTC) + timedelta(seconds=3),
    )
    assert 0 < execution_time_remaining(harness_project, load_config(harness_project), state) <= 3
    state.task_budget.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(BudgetExceededError, match="deadline"):
        execution_time_remaining(harness_project, load_config(harness_project), state)


def test_explicit_budget_replaces_only_matching_default_tripwire(harness_project: Path):
    state = continuation(harness_project, stopped(harness_project))
    config = load_config(harness_project)
    state.tool_calls = 60
    assert "tool-budget" not in active_trip_wires(harness_project, config, state)
    config.trip_wires.append(
        TripWireSpec(
            name="stricter-custom",
            metric="tool_calls",
            threshold=55,
            response="stop-and-preserve-state",
        )
    )
    assert "stricter-custom" in active_trip_wires(harness_project, config, state)


def test_emergency_stop_blocks_continuation(harness_project: Path):
    source = stopped(harness_project)
    HarnessPaths(harness_project).stop_file.write_text("operator stop")
    with pytest.raises(HarnessStoppedError):
        continuation(harness_project, source)


def test_legacy_task_has_no_new_wire_field(harness_project: Path):
    assert "task_budget" not in start_task(harness_project, "legacy").model_dump(mode="json")


def test_cli_continuation_uses_explicit_limits_and_approval(harness_project: Path):
    source = stopped(harness_project)
    args = [
        "continue",
        "finish authorized work",
        "--root",
        str(harness_project),
        "--source",
        source,
        "--max-wall-seconds",
        "43200",
        "--max-tools",
        "100",
        "--max-tokens",
        "1000000",
        "--max-cost-usd",
        "5",
        "--deadline",
        (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        "--reason",
        "explicit user continuation",
    ]
    runner = CliRunner()
    denied = runner.invoke(app, args)
    assert isinstance(denied.exception, PolicyBlockedError)
    result = runner.invoke(app, [*args, "--approved"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["task_budget"]["limits"]["max_tokens"] == 1_000_000
    assert data["tool_calls"] == 4


def test_actual_command_obeys_task_scope_without_changing_project_defaults(harness_project: Path):
    state = continuation(harness_project, stopped(harness_project))
    state.tool_calls = 60
    save_task(harness_project, state)
    config = load_config(harness_project)
    result = run_task_command(
        harness_project, config, task_id=state.task_id, argv=config.sensors[0].argv
    )
    assert result.exit_code == 0
    assert load_task(harness_project, state.task_id).tool_calls == 61
    assert load_config(harness_project).budgets.max_tool_calls == 50
