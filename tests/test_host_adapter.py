from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from hexaharness.cli import app
from hexaharness.config import load_config, save_config
from hexaharness.errors import PolicyBlockedError
from hexaharness.host_adapter import (
    prepare_host_request,
    receive_host_result,
    request_host_cancellation,
)
from hexaharness.improvement import begin_improvement, evaluate_candidate
from hexaharness.improvement_models import HostAdapterSpec, HostResult, ImprovementConfig, TextCheck
from hexaharness.paths import HarnessPaths
from hexaharness.state import load_task, save_task, start_task


@pytest.fixture
def host_experiment(harness_project: Path):
    root = harness_project
    (root / "src").mkdir()
    (root / "src/prompt.txt").write_text("Answer carefully.")
    config = load_config(root)
    config.improvement = ImprovementConfig(
        target="src/prompt.txt",
        checks=[TextCheck(name="private-check", text="carefully", required=True)],
    )
    save_config(root, config)
    task = start_task(root, "improve a text artifact")
    begin_improvement(root, config, task.task_id)
    return root, task.task_id


def spec(**changes):
    return HostAdapterSpec(
        adapter_id="fixture-adapter",
        model_id="fixture-model",
        token_limit_enforced=True,
        cost_limit_enforced=True,
        cancellation_supported=True,
        reconciliation_supported=True,
    ).model_copy(update=changes)


def prepare(root, task, **changes):
    args = {
        "key": "draft",
        "adapter": spec(),
        "goal": "clarify the wording",
        "max_tokens": 100,
        "max_cost_usd": 0.1,
        "timeout_seconds": 30,
    }
    args.update(changes)
    return prepare_host_request(root, load_config(root), task, **args)


def receipt(request, **changes):
    data = {
        "call_id": request.call_id,
        "adapter_id": request.adapter_id,
        "model_id": request.model_id,
        "request_sha256": request.request_sha256,
        "status": "completed",
        "provenance": "fixture",
        "provider_request_id": "fixture-1",
        "text": "Answer carefully and cite sources.",
        "tokens": 80,
        "cost_usd": 0.08,
    }
    data.update(changes)
    return HostResult(**data)


def test_handoff_reserves_once_and_excludes_private_evaluator(host_experiment):
    root, task = host_experiment
    first = prepare(root, task)
    second = prepare(root, task)
    assert first.created and not second.created
    assert first.request == second.request
    payload = first.request.model_dump_json()
    assert "private-check" not in payload and "checks" not in payload
    assert first.request.parents == {"baseline": "Answer carefully."}
    assert first.request.tools == "disabled" and first.request.max_calls == 1
    state = load_task(root, task)
    assert state.tokens_used == 100 and state.cost_usd == 0.1 and state.tool_calls == 1
    with pytest.raises(PolicyBlockedError, match="intent"):
        prepare(root, task, goal="another purpose")


def test_reservation_delay_cannot_extend_the_host_call_deadline(host_experiment, monkeypatch):
    import hexaharness.host_adapter as adapter

    root, task = host_experiment
    clock = [adapter.utc_now()]
    expected_deadline = clock[0] + timedelta(seconds=30)
    original = adapter.request_candidate

    def slow_reservation(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] += timedelta(seconds=31)
        return result

    monkeypatch.setattr(adapter, "utc_now", lambda: clock[0])
    monkeypatch.setattr(adapter, "request_candidate", slow_reservation)
    transfer = prepare(root, task, timeout_seconds=30)
    assert transfer.request.deadline_at <= expected_deadline
    assert (
        transfer.request.deadline_at < clock[0]
    )  # The host must not dispatch an expired envelope.
    state = load_task(root, task)
    assert state.tokens_used == 100 and state.cost_usd == 0.1
    assert state.improvement.requests["draft"].host_call.request == transfer.request


@pytest.mark.parametrize(
    "capability",
    [
        "token_limit_enforced",
        "cost_limit_enforced",
        "cancellation_supported",
        "reconciliation_supported",
    ],
)
def test_unconfirmed_host_controls_block_before_reservation(host_experiment, capability):
    root, task = host_experiment
    with pytest.raises(PolicyBlockedError, match="host"):
        prepare(root, task, adapter=spec(**{capability: False}))
    assert load_task(root, task).tokens_used == 0


def test_receipt_identity_and_usage_are_bound_and_duplicates_are_safe(host_experiment):
    root, task = host_experiment
    request = prepare(root, task).request
    with pytest.raises(PolicyBlockedError, match="identity"):
        receive_host_result(
            root, load_config(root), task, key="draft", result=receipt(request, model_id="other")
        )
    result = receive_host_result(
        root, load_config(root), task, key="draft", result=receipt(request)
    )
    again = receive_host_result(root, load_config(root), task, key="draft", result=receipt(request))
    assert result.candidate_id == again.candidate_id
    assert load_task(root, task).cost_usd == 0.1
    call = load_task(root, task).improvement.requests["draft"].host_call
    assert call.provenance == "fixture" and call.status == "completed"
    with pytest.raises(PolicyBlockedError):
        receive_host_result(
            root, load_config(root), task, key="draft", result=receipt(request, tokens=1)
        )


def test_unknown_usage_is_not_zero_and_later_receipt_reconciles(host_experiment):
    root, task = host_experiment
    request = prepare(root, task).request
    receive_host_result(
        root, load_config(root), task, key="draft", result=receipt(request, cost_usd=None)
    )
    with pytest.raises(PolicyBlockedError, match="usage"):
        evaluate_candidate(root, load_config(root), task, candidate_id="baseline", key="eval")
    receive_host_result(root, load_config(root), task, key="draft", result=receipt(request))
    assert load_task(root, task).improvement.requests["draft"].status == "settled"


def test_uncertain_result_does_not_authorize_redispatch(host_experiment):
    root, task = host_experiment
    request = prepare(root, task).request
    receive_host_result(
        root,
        load_config(root),
        task,
        key="draft",
        result=receipt(
            request,
            status="unknown",
            text=None,
            tokens=None,
            cost_usd=None,
            provider_request_id=None,
        ),
    )
    assert not prepare(root, task).created
    state = load_task(root, task)
    assert state.improvement.requests["draft"].candidate_id is None
    assert state.improvement.requests["draft"].status == "unknown"
    receive_host_result(root, load_config(root), task, key="draft", result=receipt(request))
    assert load_task(root, task).improvement.requests["draft"].status == "settled"


def test_cancellation_keeps_unknown_usage_until_provider_receipt(host_experiment):
    root, task = host_experiment
    request = prepare(root, task).request
    directive = request_host_cancellation(root, task, key="draft")
    assert directive.call_id == request.call_id
    assert load_task(root, task).improvement.requests["draft"].status == "pending"
    result = receive_host_result(
        root,
        load_config(root),
        task,
        key="draft",
        result=receipt(
            request,
            status="cancelled",
            text=None,
            tokens=20,
            cost_usd=0.02,
        ),
    )
    assert result is None
    assert load_task(root, task).improvement.requests["draft"].status == "settled"


def test_overage_is_charged_and_blocks_another_host_request(host_experiment):
    root, task = host_experiment
    request = prepare(root, task).request
    receive_host_result(
        root, load_config(root), task, key="draft", result=receipt(request, tokens=150)
    )
    assert load_task(root, task).tokens_used == 150
    with pytest.raises(PolicyBlockedError):
        prepare(root, task, key="next")


def test_malformed_receipts_and_false_cancellation_do_not_mutate_state(host_experiment):
    root, task = host_experiment
    request = prepare(root, task).request
    for changes in (
        {"tokens": True},
        {"cost_usd": float("nan")},
        {"text": None},
        {"schema_version": 2},
    ):
        with pytest.raises(ValidationError):
            receipt(request, **changes)
    with pytest.raises(PolicyBlockedError):
        request_host_cancellation(root, task, key="missing")
    assert not HarnessPaths(root).improvement_stop_file(task).exists()


def test_modified_request_payload_is_detected_before_accepting_receipt(host_experiment):
    root, task = host_experiment
    request = prepare(root, task).request
    state = load_task(root, task)
    state.improvement.requests["draft"].host_call.request.goal = "tampered"
    save_task(root, state)
    with pytest.raises(PolicyBlockedError, match="identity"):
        receive_host_result(root, load_config(root), task, key="draft", result=receipt(request))


@pytest.mark.parametrize("field", ["goal", "provider", "digest", "usage", "adapter", "missing"])
def test_fresh_evaluation_rechecks_retained_host_evidence(host_experiment, field):
    root, task = host_experiment
    envelope = prepare(root, task).request
    receive_host_result(root, load_config(root), task, key="draft", result=receipt(envelope))
    state = load_task(root, task)
    request = state.improvement.requests["draft"]
    call = request.host_call
    if field == "goal":
        call.request.goal = "changed after settlement"
    elif field == "provider":
        call.provider_request_id = "another-call"
    elif field == "digest":
        call.receipt_sha256 = "0" * 64
    elif field == "usage":
        request.actual_tokens = 1
    elif field == "adapter":
        call.adapter.model_id = "another-model"
    else:
        call.receipt_sha256 = None
    save_task(root, state)
    with pytest.raises(PolicyBlockedError, match="host"):
        evaluate_candidate(root, load_config(root), task, candidate_id="baseline", key="fresh")


def test_candidate_text_is_not_executed_by_independent_literal_evaluation(host_experiment):
    root, task = host_experiment
    request = prepare(root, task).request
    marker = root / "must-not-exist"
    malicious_text = f"carefully; __import__('pathlib').Path({str(marker)!r}).write_text('bad')"
    candidate = receive_host_result(
        root, load_config(root), task, key="draft", result=receipt(request, text=malicious_text)
    )
    result = evaluate_candidate(
        root, load_config(root), task, candidate_id=candidate.candidate_id, key="data"
    )
    assert result.status == "passed"
    assert not marker.exists()


def test_cli_host_request_and_receipt_round_trip(host_experiment):
    root, task = host_experiment
    (root / "src/adapter.json").write_text(spec().model_dump_json())
    runner = CliRunner()
    response = runner.invoke(
        app,
        [
            "improve",
            task,
            "--root",
            str(root),
            "--action",
            "host-request",
            "--key",
            "cli",
            "--adapter-file",
            "src/adapter.json",
            "--goal",
            "clarify",
            "--max-tokens",
            "100",
            "--max-cost-usd",
            "0.1",
        ],
    )
    assert response.exit_code == 0, response.output
    from hexaharness.improvement_models import HostRequest

    request = HostRequest.model_validate(json.loads(response.stdout)["result"]["request"])
    (root / "src/receipt.json").write_text(receipt(request).model_dump_json())
    response = runner.invoke(
        app,
        [
            "improve",
            task,
            "--root",
            str(root),
            "--action",
            "host-result",
            "--key",
            "cli",
            "--receipt-file",
            "src/receipt.json",
        ],
    )
    assert response.exit_code == 0, response.output
    assert json.loads(response.stdout)["result"]["text"] == "Answer carefully and cite sources."
