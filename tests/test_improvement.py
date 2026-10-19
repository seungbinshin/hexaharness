from __future__ import annotations

from pathlib import Path

import pytest

from hexaharness.config import load_config, save_config
from hexaharness.errors import PolicyBlockedError, VerificationFailedError
from hexaharness.improvement import (
    begin_improvement,
    cancel_improvement,
    evaluate_candidate,
    promote_candidate,
    request_candidate,
    resume_improvement,
    submit_candidate,
)
from hexaharness.improvement_models import ImprovementConfig, TextCheck
from hexaharness.state import load_task, save_task, start_task


def setup_experiment(root: Path) -> str:
    (root / "src").mkdir(exist_ok=True)
    (root / "src/prompt.txt").write_text("Answer carefully.", encoding="utf-8")
    config = load_config(root)
    config.improvement = ImprovementConfig(
        target="src/prompt.txt",
        checks=[
            TextCheck(name="baseline", text="carefully", required=True),
            TextCheck(name="citations", text="cite sources", weight=2),
        ],
    )
    save_config(root, config)
    task_id = start_task(root, "Improve prompt on fixed literal checks").task_id
    begin_improvement(root, config, task_id)
    return task_id


def test_improvement_is_disabled_by_default_without_state_changes(harness_project: Path) -> None:
    config = load_config(harness_project)
    assert config.improvement is None
    task_id = start_task(harness_project, "Ordinary stable workflow").task_id
    before = load_task(harness_project, task_id).model_dump_json()
    with pytest.raises(PolicyBlockedError, match="configure improvement"):
        begin_improvement(harness_project, config, task_id)
    assert load_task(harness_project, task_id).model_dump_json() == before


def test_compare_promote_preserves_baseline_and_uses_parent_budget(harness_project: Path) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    config = load_config(root)
    request_candidate(root, config, task_id, key="draft-1", max_tokens=100, max_cost_usd=0.25)
    candidate = submit_candidate(
        root,
        config,
        task_id,
        key="draft-1",
        text="Answer carefully; cite sources.",
        tokens=80,
        cost_usd=0.2,
    )
    evaluate_candidate(root, config, task_id, candidate_id="baseline", key="baseline-1")
    evaluate_candidate(root, config, task_id, candidate_id=candidate.candidate_id, key="trial-1")
    result = promote_candidate(root, config, task_id, candidate.candidate_id)
    state = load_task(root, task_id)
    assert result.status == "promoted"
    assert result.candidates["baseline"].text == "Answer carefully."
    assert (root / "src/prompt.txt").read_text() == "Answer carefully; cite sources."
    assert state.tokens_used == 100  # conservative reservation: no refund of unknown overhead
    assert state.cost_usd == 0.25
    assert state.tool_calls >= 4  # generation, two comparisons, required sensor


def test_forced_initialization_preserves_active_improvement_config(harness_project: Path) -> None:
    from hexaharness.scaffold import initialize_project

    task_id = setup_experiment(harness_project)
    before = load_config(harness_project)
    initialize_project(harness_project, force=True)
    after = load_config(harness_project)
    assert after.improvement == before.improvement
    evaluate_candidate(harness_project, after, task_id, candidate_id="baseline", key="after-init")


def test_duplicate_submission_does_not_reset_budget_or_create_candidates(
    harness_project: Path,
) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    config = load_config(root)
    request_candidate(root, config, task_id, key="draft-1", max_tokens=100, max_cost_usd=0)
    first = submit_candidate(
        root, config, task_id, key="draft-1", text="carefully", tokens=10, cost_usd=0
    )
    again = submit_candidate(
        root, config, task_id, key="draft-1", text="carefully", tokens=10, cost_usd=0
    )
    assert again == first
    assert load_task(root, task_id).tokens_used == 100
    with pytest.raises(PolicyBlockedError, match="different"):
        submit_candidate(
            root, config, task_id, key="draft-1", text="changed", tokens=10, cost_usd=0
        )


def test_unknown_host_usage_cannot_buy_another_trial(harness_project: Path) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    config = load_config(root)
    request_candidate(root, config, task_id, key="draft-1", max_tokens=100, max_cost_usd=0.1)
    submit_candidate(
        root, config, task_id, key="draft-1", text="carefully", tokens=None, cost_usd=None
    )
    with pytest.raises(PolicyBlockedError, match="usage"):
        request_candidate(root, config, task_id, key="draft-2", max_tokens=100, max_cost_usd=0.1)
    with pytest.raises(PolicyBlockedError, match="usage"):
        evaluate_candidate(root, config, task_id, candidate_id="baseline", key="baseline-1")


def test_changed_evaluator_invalidates_existing_scores(harness_project: Path) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    config = load_config(root)
    evaluate_candidate(root, config, task_id, candidate_id="baseline", key="baseline-1")
    assert config.improvement is not None
    config.improvement.checks[0].text = "changed"
    save_config(root, config)
    with pytest.raises(PolicyBlockedError, match="configuration"):
        evaluate_candidate(root, config, task_id, candidate_id="baseline", key="baseline-2")


def ready_candidate(root: Path, task_id: str) -> str:
    config = load_config(root)
    request_candidate(root, config, task_id, key="draft", max_tokens=0, max_cost_usd=0)
    candidate = submit_candidate(
        root, config, task_id, key="draft", text="carefully; cite sources", tokens=0, cost_usd=0
    )
    evaluate_candidate(root, config, task_id, candidate_id="baseline", key="base")
    evaluate_candidate(root, config, task_id, candidate_id=candidate.candidate_id, key="first")
    return candidate.candidate_id


@pytest.mark.parametrize("action", ["repeat-promotion", "complete"])
def test_promoted_candidate_still_requires_retained_baseline_evidence(
    harness_project: Path,
    action: str,
) -> None:
    from hexaharness.workflow import complete_task

    root = harness_project
    task_id = setup_experiment(root)
    config = load_config(root)
    candidate_id = ready_candidate(root, task_id)
    promote_candidate(root, config, task_id, candidate_id)
    experiment = load_task(root, task_id).improvement
    assert experiment is not None
    baseline = experiment.attempts[experiment.candidates["baseline"].latest_attempt]
    (root / baseline.output_path).unlink()
    with pytest.raises(VerificationFailedError, match="missing"):
        if action == "repeat-promotion":
            promote_candidate(root, config, task_id, candidate_id)
        else:
            complete_task(root, config, task_id, artifacts=["src/prompt.txt"])


def test_failed_rerun_cannot_reuse_old_passing_score(
    harness_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    candidate_id = ready_candidate(root, task_id)
    config = load_config(root)

    def interrupt(*args: object) -> None:
        raise RuntimeError("interrupted evaluator")

    monkeypatch.setattr("hexaharness.improvement._score", interrupt)
    with pytest.raises(RuntimeError):
        evaluate_candidate(root, config, task_id, candidate_id=candidate_id, key="second")
    with pytest.raises(VerificationFailedError, match="fresh"):
        promote_candidate(root, config, task_id, candidate_id)
    resume_improvement(root, config, task_id)
    again = evaluate_candidate(root, config, task_id, candidate_id=candidate_id, key="second")
    assert again.status == "interrupted"
    assert again.score is None


def test_tampered_output_blocks_promotion(harness_project: Path) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    candidate_id = ready_candidate(root, task_id)
    experiment = load_task(root, task_id).improvement
    assert experiment is not None
    attempt = experiment.attempts[experiment.candidates[candidate_id].latest_attempt]
    (root / attempt.output_path).write_text('{"score": 1000}')
    with pytest.raises(VerificationFailedError, match="changed"):
        promote_candidate(root, load_config(root), task_id, candidate_id)


def test_promotion_will_not_overwrite_user_edits(harness_project: Path) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    candidate_id = ready_candidate(root, task_id)
    (root / "src/prompt.txt").write_text("user edit")
    with pytest.raises(PolicyBlockedError, match="target changed"):
        promote_candidate(root, load_config(root), task_id, candidate_id)
    assert (root / "src/prompt.txt").read_text() == "user edit"


def test_failed_required_sensor_rolls_back_candidate(harness_project: Path) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    # A real subprocess sensor which succeeds on baseline, fails on the candidate.
    config = load_config(root)
    command = [
        config.sensors[0].argv[0],
        "-c",
        "from pathlib import Path; "
        "raise SystemExit(1 if 'cite sources' in Path('src/prompt.txt').read_text() else 0)",
    ]
    config.sensors[0].argv = command
    config.policy.allow_execute.append(command)
    save_config(root, config)
    # A new task starts under the new specification; the previous task is not reused/reset.
    task_id = start_task(root, "Check rollback under a fixed failing gate").task_id
    begin_improvement(root, config, task_id)
    candidate_id = ready_candidate(root, task_id)
    with pytest.raises(VerificationFailedError, match="sensors failed"):
        promote_candidate(root, config, task_id, candidate_id)
    assert (root / "src/prompt.txt").read_text() == "Answer carefully."
    assert load_task(root, task_id).improvement.status == "active"
    assert load_task(root, task_id).status.value == "paused"


def test_cancel_resume_preserves_budgets_and_baseline(harness_project: Path) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    candidate_id = ready_candidate(root, task_id)
    before = load_task(root, task_id)
    cancel_improvement(root, task_id)
    with pytest.raises(PolicyBlockedError, match="cancellation"):
        evaluate_candidate(
            root, load_config(root), task_id, candidate_id=candidate_id, key="second"
        )
    resume_improvement(root, load_config(root), task_id)
    after = load_task(root, task_id)
    assert after.tool_calls == before.tool_calls
    assert after.tokens_used == before.tokens_used
    assert after.improvement.candidates["baseline"] == before.improvement.candidates["baseline"]


def test_crashed_promotion_is_reconciled_without_automatic_reexecution(
    harness_project: Path,
) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    candidate_id = ready_candidate(root, task_id)
    state = load_task(root, task_id)
    state.improvement.status = "promoting"
    state.improvement.selected_candidate = candidate_id
    save_task(root, state)
    (root / "src/prompt.txt").write_text(state.improvement.candidates[candidate_id].text)
    resume_improvement(root, load_config(root), task_id)
    assert (root / "src/prompt.txt").read_text() == "Answer carefully."
    assert load_task(root, task_id).tool_calls == state.tool_calls


def test_reservation_cannot_exceed_remaining_parent_budget(harness_project: Path) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    config = load_config(root)
    state = load_task(root, task_id)
    state.tokens_used = config.budgets.max_tokens - 10
    save_task(root, state)
    with pytest.raises(PolicyBlockedError, match="remaining task budget"):
        request_candidate(root, config, task_id, key="expensive", max_tokens=11, max_cost_usd=0)
    assert load_task(root, task_id).tokens_used == state.tokens_used


def test_duplicate_request_tells_host_not_to_dispatch_again(harness_project: Path) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    config = load_config(root)
    first = request_candidate(root, config, task_id, key="one", max_tokens=10, max_cost_usd=0)
    repeated = request_candidate(root, config, task_id, key="one", max_tokens=10, max_cost_usd=0)
    assert first.created is True
    assert repeated.created is False
    assert load_task(root, task_id).tokens_used == 10


def test_cancelled_capture_never_launches_command(
    harness_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hexaharness.execution import execute_capture

    root = harness_project
    task_id = setup_experiment(root)
    cancel_improvement(root, task_id)

    def unexpected_launch(*args: object, **kwargs: object) -> None:
        pytest.fail("cancelled command was launched")

    monkeypatch.setattr("hexaharness.execution.subprocess.Popen", unexpected_launch)
    result = execute_capture(
        root, task_id=task_id, label="cancel", argv=["unused"], timeout_seconds=1
    )
    assert result.stopped


def test_future_experiment_schema_is_rejected_without_rewriting_state(
    harness_project: Path,
) -> None:
    import json

    from pydantic import ValidationError

    from hexaharness.paths import HarnessPaths

    root = harness_project
    task_id = setup_experiment(root)
    path = HarnessPaths(root).state_file(task_id)
    data = json.loads(path.read_text())
    data["improvement"]["schema_version"] = 99
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(ValidationError):
        load_task(root, task_id)
    assert path.read_bytes() == before


def test_partial_usage_report_cannot_be_replaced_with_a_lower_measurement(
    harness_project: Path,
) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    config = load_config(root)
    request_candidate(root, config, task_id, key="partial", max_tokens=10, max_cost_usd=0.1)
    submit_candidate(
        root, config, task_id, key="partial", text="carefully", tokens=12, cost_usd=None
    )
    with pytest.raises(PolicyBlockedError, match="reported usage"):
        submit_candidate(
            root, config, task_id, key="partial", text="carefully", tokens=10, cost_usd=0
        )


def test_composition_records_parents_and_actual_overrun(harness_project: Path) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    first = ready_candidate(root, task_id)
    config = load_config(root)
    request_candidate(
        root,
        config,
        task_id,
        key="compose",
        parents=["baseline", first],
        max_tokens=10,
        max_cost_usd=0.01,
    )
    candidate = submit_candidate(
        root, config, task_id, key="compose", text="carefully", tokens=12, cost_usd=0.02
    )
    assert candidate.parents == ["baseline", first]
    state = load_task(root, task_id)
    assert state.tokens_used == 12
    assert state.cost_usd == 0.02
    with pytest.raises(PolicyBlockedError, match="usage"):
        evaluate_candidate(
            root, config, task_id, candidate_id=candidate.candidate_id, key="overrun"
        )


def test_higher_score_cannot_override_a_mandatory_check(harness_project: Path) -> None:
    root = harness_project
    task_id = setup_experiment(root)
    config = load_config(root)
    request_candidate(root, config, task_id, key="bad", max_tokens=0, max_cost_usd=0)
    candidate = submit_candidate(
        root, config, task_id, key="bad", text="cite sources", tokens=0, cost_usd=0
    )
    evaluate_candidate(root, config, task_id, candidate_id="baseline", key="base")
    outcome = evaluate_candidate(
        root, config, task_id, candidate_id=candidate.candidate_id, key="bad"
    )
    assert outcome.score == 2
    assert outcome.status == "failed"
    with pytest.raises(VerificationFailedError, match="hard checks"):
        promote_candidate(root, config, task_id, candidate.candidate_id)


@pytest.mark.parametrize(
    "target", ["src/tool.py", "AGENTS.md", "../escape.txt", ".hexaharness/GUIDES.md"]
)
def test_unsupported_targets_are_never_read(harness_project: Path, target: str) -> None:
    root = harness_project
    config = load_config(root)
    config.improvement = ImprovementConfig(target=target, checks=[TextCheck(name="x", text="x")])
    save_config(root, config)
    task_id = start_task(root, "Refuse unsafe target").task_id
    with pytest.raises(PolicyBlockedError):
        begin_improvement(root, config, task_id)
    assert load_task(root, task_id).improvement is None


def test_cli_uses_existing_app_for_complete_improvement_flow(harness_project: Path) -> None:
    import json

    from typer.testing import CliRunner

    from hexaharness.cli import app

    root = harness_project
    task_id = setup_experiment(root)
    runner = CliRunner()

    def invoke(action: str, *args: str) -> dict:
        result = runner.invoke(
            app, ["improve", task_id, "--root", str(root), "--action", action, *args]
        )
        assert result.exit_code == 0, result.output
        response = json.loads(result.stdout)
        assert response["schema_version"] == 1
        return response["result"]

    assert invoke("request", "--key", "cli")["created"] is True
    (root / "src/candidate.txt").write_text("carefully; cite sources")
    candidate = invoke(
        "submit",
        "--key",
        "cli",
        "--candidate-file",
        "src/candidate.txt",
        "--tokens",
        "0",
        "--cost-usd",
        "0",
    )
    invoke("evaluate", "--key", "cli-baseline")
    invoke("evaluate", "--key", "cli-candidate", "--candidate-id", candidate["candidate_id"])
    assert invoke("promote", "--candidate-id", candidate["candidate_id"])["status"] == "promoted"
    completed = runner.invoke(
        app, ["complete", task_id, "--root", str(root), "--artifact", "src/prompt.txt"]
    )
    assert completed.exit_code == 0, completed.output
    assert json.loads(completed.stdout)["status"] == "completed"


def test_cancellation_interrupts_real_running_process(harness_project: Path) -> None:
    import sys
    import threading
    import time

    from hexaharness.execution import execute_capture

    root = harness_project
    task_id = setup_experiment(root)
    results = []
    ready = root / "ready.txt"

    def run() -> None:
        results.append(
            execute_capture(
                root,
                task_id=task_id,
                label="cancel-running",
                argv=[
                    sys.executable,
                    "-c",
                    "from pathlib import Path; import time; "
                    "Path('ready.txt').write_text('ready'); time.sleep(30)",
                ],
                timeout_seconds=10,
            )
        )

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 3
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    cancel_improvement(root, task_id)
    thread.join(timeout=4)
    assert not thread.is_alive()
    assert results[0].stopped is True
    assert results[0].timed_out is False


def test_terminal_task_cannot_rollback_or_resume(harness_project: Path) -> None:
    from hexaharness.models import TaskStatus
    from hexaharness.state import set_task_status

    root = harness_project
    task_id = setup_experiment(root)
    candidate_id = ready_candidate(root, task_id)
    state = load_task(root, task_id)
    state.improvement.status = "promoting"
    state.improvement.selected_candidate = candidate_id
    save_task(root, state)
    target = root / "src/prompt.txt"
    target.write_text(state.improvement.candidates[candidate_id].text)
    set_task_status(root, task_id, TaskStatus.STOPPED)
    with pytest.raises(PolicyBlockedError, match="terminal"):
        resume_improvement(root, load_config(root), task_id)
    assert target.read_text() == state.improvement.candidates[candidate_id].text
