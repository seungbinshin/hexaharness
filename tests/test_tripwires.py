from __future__ import annotations

from pathlib import Path

from hexaharness.config import load_config
from hexaharness.events import iter_events
from hexaharness.models import TripWireSpec
from hexaharness.state import start_task
from hexaharness.tripwires import active_trip_wires


def test_custom_trip_wire_threshold_and_response_are_executable(
    harness_project: Path,
) -> None:
    config = load_config(harness_project)
    config.trip_wires = [
        TripWireSpec(
            name="early-tool-warning",
            metric="tool_calls",
            threshold=1,
            response="pause-and-inspect",
        )
    ]
    state = start_task(harness_project, "Exercise configured trip wire")
    state.tool_calls = 1

    assert active_trip_wires(harness_project, config, state) == ["early-tool-warning"]
    event = list(iter_events(harness_project))[-1]
    assert event.event_type == "trip-wire.fired"
    assert event.payload == {
        "name": "early-tool-warning",
        "response": "pause-and-inspect",
    }


def test_zero_cost_budget_prohibits_positive_spend(harness_project: Path) -> None:
    config = load_config(harness_project)
    config.budgets.max_cost_usd = 0
    config.trip_wires = []
    state = start_task(harness_project, "Zero-spend task")

    assert active_trip_wires(harness_project, config, state) == []

    state.cost_usd = 0.01
    assert active_trip_wires(harness_project, config, state) == ["cost-budget"]
