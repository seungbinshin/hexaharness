"""Host-driven text improvement. Candidates are data, never executable code."""

from __future__ import annotations

import hashlib
import math
import uuid
from pathlib import Path

from hexaharness.config import ensure_configuration_current, ensure_configuration_ready
from hexaharness.errors import PolicyBlockedError, VerificationFailedError
from hexaharness.events import record_event
from hexaharness.improvement_models import (
    Candidate,
    EvaluationAttempt,
    GenerationRequest,
    GenerationResponse,
    ImprovementConfig,
    ImprovementState,
)
from hexaharness.io import atomic_write_text, read_json, write_json
from hexaharness.models import HarnessConfig, PolicyDecision, TaskState, TaskStatus
from hexaharness.paths import HarnessPaths
from hexaharness.policy import evaluate_path
from hexaharness.runner import execution_time_remaining
from hexaharness.sensors import required_sensors_passed, run_sensors
from hexaharness.state import (
    configuration_fingerprint,
    configuration_lock,
    guidance_fingerprint,
    is_external_action_marker,
    load_task,
    record_sensor_results,
    record_write_observation,
    required_sensor_evidence_is_current,
    save_task,
    set_task_status,
    task_lock,
)
from hexaharness.tripwires import active_trip_wires


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise PolicyBlockedError("improvement paths must stay inside the project")
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise PolicyBlockedError("improvement paths cannot contain symlinks")
    if current.exists() and not current.is_file():
        raise PolicyBlockedError("improvement evidence must be a regular file")
    return current


def _target(root: Path, config: HarnessConfig, relative: str) -> Path:
    path = _safe_file(root, relative)
    if (
        path.suffix.lower() not in {".txt", ".md"}
        or any(part.startswith(".") for part in Path(relative).parts)
        or path.name.upper() in {"AGENTS.MD", "CLAUDE.MD", "SKILL.MD", "GUIDES.MD", "SECURITY.MD"}
        or Path(relative).parts[0] in {"skills", "tests"}
    ):
        raise PolicyBlockedError("only text data artifacts may be improved, not policy or code")
    for write in (False, True):
        if evaluate_path(config, root, path, write=write).decision != PolicyDecision.ALLOW:
            raise PolicyBlockedError("improvement target requires an allowed read/write path")
    return path


def _read_text(path: Path) -> str:
    if path.stat().st_size > 65536:
        raise PolicyBlockedError("text artifact exceeds the 64 KiB limit")
    return path.read_bytes().decode("utf-8")


def _get(state: TaskState) -> ImprovementState:
    if state.improvement is None:
        raise PolicyBlockedError("task has no improvement experiment")
    return state.improvement


def _candidate(experiment: ImprovementState, candidate_id: str) -> Candidate:
    if candidate_id not in experiment.candidates:
        raise PolicyBlockedError("unknown improvement candidate")
    return experiment.candidates[candidate_id]


def _fresh(root: Path, config: HarnessConfig, experiment: ImprovementState) -> None:
    ensure_configuration_current(root, config)
    if configuration_fingerprint(config) != experiment.config_sha256:
        raise PolicyBlockedError("experiment configuration changed; existing scores are invalid")
    if guidance_fingerprint(root) != experiment.guidance_sha256:
        raise PolicyBlockedError("project guidance changed; existing scores are invalid")
    if _digest(experiment.specification.model_dump_json()) != experiment.evaluator_sha256:
        raise PolicyBlockedError("independent evaluator specification is no longer intact")
    for candidate in experiment.candidates.values():
        if _digest(candidate.text) != candidate.sha256:
            raise PolicyBlockedError("candidate or immutable baseline changed")


def _ready(root: Path, config: HarnessConfig, state: TaskState) -> ImprovementState:
    experiment = _get(state)
    _fresh(root, config, experiment)
    if state.status != TaskStatus.ACTIVE or experiment.status != "active":
        raise PolicyBlockedError("task and experiment must be active; reconcile or resume first")
    paths = HarnessPaths(root)
    if paths.stop_file.exists() or paths.improvement_stop_file(state.task_id).exists():
        raise PolicyBlockedError("improvement cancellation or emergency stop is active")
    if is_external_action_marker(state.next_step):
        raise PolicyBlockedError("resolve the existing external action before improvement")
    if any(request.status != "settled" for request in experiment.requests.values()):
        raise PolicyBlockedError("host usage is pending, unknown or over budget; reconcile first")
    execution_time_remaining(root, config, state)
    return experiment


def _save(root: Path, state: TaskState, event: str, **payload: str) -> None:
    save_task(root, state)
    record_event(
        root,
        f"improvement.{event}",
        task_id=state.task_id,
        payload={"schema_version": 1, **payload},
    )


def begin_improvement(root: Path, config: HarnessConfig, task_id: str) -> ImprovementState:
    with task_lock(root, task_id), configuration_lock(root):
        ensure_configuration_current(root, config)
        ensure_configuration_ready(config, root)
        state = load_task(root, task_id)
        if state.improvement is not None:
            _fresh(root, config, state.improvement)
            return state.improvement
        if config.improvement is None:
            raise PolicyBlockedError("configure improvement in the existing harness.yaml first")
        if state.status != TaskStatus.ACTIVE or is_external_action_marker(state.next_step):
            raise PolicyBlockedError("begin requires an active task without an external action")
        if HarnessPaths(root).stop_file.exists():
            raise PolicyBlockedError("emergency stop is active")
        execution_time_remaining(root, config, state)
        specification = config.improvement.model_copy(deep=True)
        text = _read_text(_target(root, config, specification.target))
        experiment = ImprovementState(
            target=specification.target,
            config_sha256=configuration_fingerprint(config),
            guidance_sha256=guidance_fingerprint(root),
            evaluator_sha256=_digest(specification.model_dump_json()),
            specification=specification,
            candidates={
                "baseline": Candidate(candidate_id="baseline", text=text, sha256=_digest(text))
            },
        )
        state.improvement = experiment
        _save(root, state, "began", baseline_sha256=_digest(text))
        return experiment


def request_candidate(
    root: Path,
    config: HarnessConfig,
    task_id: str,
    *,
    key: str,
    max_tokens: int,
    max_cost_usd: float,
    parents: list[str] | None = None,
) -> GenerationResponse:
    request = GenerationRequest(
        key=key,
        parents=parents or ["baseline"],
        reserved_tokens=max_tokens,
        reserved_cost_usd=max_cost_usd,
    )
    with task_lock(root, task_id), configuration_lock(root):
        state = load_task(root, task_id)
        experiment = _get(state)
        if existing := experiment.requests.get(key):
            if (
                existing.parents != request.parents
                or existing.reserved_tokens != max_tokens
                or existing.reserved_cost_usd != max_cost_usd
            ):
                raise PolicyBlockedError("idempotency key was already used for a different request")
            return GenerationResponse(created=False, request=existing)
        _ready(root, config, state)
        if len(experiment.requests) >= experiment.specification.max_candidates:
            raise PolicyBlockedError("experiment candidate budget is exhausted")
        if len(set(request.parents)) != len(request.parents) or any(
            parent not in experiment.candidates for parent in request.parents
        ):
            raise PolicyBlockedError("composition parents must be distinct existing candidates")
        if (
            state.tokens_used + max_tokens > config.budgets.max_tokens
            or state.cost_usd + max_cost_usd > config.budgets.max_cost_usd
        ):
            raise PolicyBlockedError("generation reservation exceeds the remaining task budget")
        # Charge before dispatch. Unknown/crashed calls keep their conservative reservation.
        state.tokens_used += max_tokens
        state.cost_usd += max_cost_usd
        state.tool_calls += 1
        experiment.requests[key] = request
        _save(root, state, "generation-reserved", key=key)
        return GenerationResponse(created=True, request=request)


def submit_candidate(
    root: Path,
    config: HarnessConfig,
    task_id: str,
    *,
    key: str,
    text: str,
    tokens: int | None,
    cost_usd: float | None,
) -> Candidate:
    if (tokens is not None and tokens < 0) or (
        cost_usd is not None and (not math.isfinite(cost_usd) or cost_usd < 0)
    ):
        raise ValueError("usage must be finite and nonnegative")
    if len(text.encode("utf-8")) > 65536:
        raise ValueError("candidate exceeds the 64 KiB limit")
    with task_lock(root, task_id):
        state = load_task(root, task_id)
        experiment = _get(state)
        request = experiment.requests.get(key)
        if request is None:
            raise PolicyBlockedError("reserve generation before submitting a candidate")
        if (request.actual_tokens is not None and request.actual_tokens != tokens) or (
            request.actual_cost_usd is not None and request.actual_cost_usd != cost_usd
        ):
            raise PolicyBlockedError("previously reported usage cannot be changed")
        if request.candidate_id is not None:
            existing = experiment.candidates[request.candidate_id]
            if existing.text != text:
                raise PolicyBlockedError("idempotency key was already used for different output")
            if request.status in {"settled", "over-budget"}:
                if request.actual_tokens != tokens or request.actual_cost_usd != cost_usd:
                    raise PolicyBlockedError("settled usage cannot be replaced by different usage")
                return existing
        else:
            candidate_id = f"candidate-{uuid.uuid4().hex}"
            existing = Candidate(
                candidate_id=candidate_id, text=text, sha256=_digest(text), parents=request.parents
            )
            experiment.candidates[candidate_id] = existing
            request.candidate_id = candidate_id
        # Charge known overruns immediately, even if the other measurement is still unknown.
        extra_tokens = max(0, (tokens or 0) - request.reserved_tokens)
        extra_cost = max(0.0, (cost_usd or 0.0) - request.reserved_cost_usd)
        state.tokens_used += extra_tokens - max(
            0, (request.actual_tokens or 0) - request.reserved_tokens
        )
        state.cost_usd += extra_cost - max(
            0.0, (request.actual_cost_usd or 0.0) - request.reserved_cost_usd
        )
        request.actual_tokens, request.actual_cost_usd = tokens, cost_usd
        if tokens is None or cost_usd is None:
            request.status = "unknown"
        else:
            request.status = "over-budget" if extra_tokens or extra_cost else "settled"
        # Usage reconciliation never dispatches work; accept late reports after a stop.
        _save(root, state, "generation-reported", key=key, usage=request.status)
        return existing


def _score(text: str, specification: ImprovementConfig) -> tuple[float, bool, dict[str, bool]]:
    checks = {check.name: (check.text in text) == check.present for check in specification.checks}
    score = sum(check.weight for check in specification.checks if checks[check.name])
    passed = all(checks[check.name] for check in specification.checks if check.required)
    return score, passed, checks


def evaluate_candidate(
    root: Path,
    config: HarnessConfig,
    task_id: str,
    *,
    candidate_id: str,
    key: str,
) -> EvaluationAttempt:
    with task_lock(root, task_id), configuration_lock(root):
        state = load_task(root, task_id)
        experiment = _ready(root, config, state)
        candidate = _candidate(experiment, candidate_id)
        for previous in experiment.attempts.values():
            if previous.key == key:
                if previous.candidate_id != candidate_id:
                    raise PolicyBlockedError("evaluation key belongs to a different candidate")
                return previous
        if any(attempt.status == "running" for attempt in experiment.attempts.values()):
            raise PolicyBlockedError("reconcile the interrupted evaluation before another attempt")
        attempt = EvaluationAttempt(
            attempt_id=uuid.uuid4().hex,
            key=key,
            candidate_id=candidate_id,
            candidate_sha256=candidate.sha256,
            evaluator_sha256=experiment.evaluator_sha256,
        )
        experiment.attempts[attempt.attempt_id] = attempt
        candidate.latest_attempt = attempt.attempt_id
        state.tool_calls += 1
        state.attempts += 1
        _save(root, state, "evaluation-started", attempt_id=attempt.attempt_id)
        score, passed, checks = _score(candidate.text, experiment.specification)
        paths = HarnessPaths(root)
        if paths.stop_file.exists() or paths.improvement_stop_file(task_id).exists():
            attempt.status = "interrupted"
        else:
            attempt.status = "passed" if passed else "failed"
            attempt.score = score
            output = paths.task_artifacts(task_id) / f"improvement-{attempt.attempt_id}.json"
            write_json(
                output,
                {
                    "schema_version": 1,
                    "attempt_id": attempt.attempt_id,
                    "candidate_sha256": candidate.sha256,
                    "evaluator_sha256": experiment.evaluator_sha256,
                    "score": score,
                    "passed": passed,
                    "checks": checks,
                },
            )
            attempt.output_path = output.relative_to(root).as_posix()
            attempt.output_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
        _save(
            root, state, "evaluation-finished", attempt_id=attempt.attempt_id, status=attempt.status
        )
        return attempt


def _current_attempt(
    root: Path,
    experiment: ImprovementState,
    candidate_id: str,
    *,
    require_passed: bool = False,
) -> EvaluationAttempt:
    candidate = _candidate(experiment, candidate_id)
    attempt = experiment.attempts.get(candidate.latest_attempt or "")
    if (
        attempt is None
        or attempt.status not in {"passed", "failed"}
        or (require_passed and attempt.status != "passed")
        or attempt.score is None
        or attempt.output_path is None
        or attempt.output_sha256 is None
        or attempt.candidate_sha256 != candidate.sha256
        or attempt.evaluator_sha256 != experiment.evaluator_sha256
    ):
        raise VerificationFailedError("fresh completed comparison evidence is required")
    output = _safe_file(root, attempt.output_path)
    if (
        not output.is_file()
        or hashlib.sha256(output.read_bytes()).hexdigest() != attempt.output_sha256
    ):
        raise VerificationFailedError("comparison output is missing or changed")
    report = read_json(output)
    if (
        report["attempt_id"] != attempt.attempt_id
        or report["score"] != attempt.score
        or report["candidate_sha256"] != candidate.sha256
        or report["evaluator_sha256"] != experiment.evaluator_sha256
        or report["passed"] != (attempt.status == "passed")
    ):
        raise VerificationFailedError("comparison output is bound to a different attempt")
    return attempt


def _rollback(root: Path, config: HarnessConfig, state: TaskState) -> None:
    experiment = _get(state)
    target = _target(root, config, experiment.target)
    baseline = experiment.candidates["baseline"]
    selected = experiment.candidates[experiment.selected_candidate or "baseline"]
    if _digest(baseline.text) != baseline.sha256 or _digest(selected.text) != selected.sha256:
        raise VerificationFailedError("rollback baseline or candidate evidence changed")
    current = _digest(_read_text(target))
    if current == selected.sha256:
        atomic_write_text(target, baseline.text)
    elif current != baseline.sha256:
        experiment.status = "conflict"
        _save(root, state, "promotion-conflict")
        raise PolicyBlockedError("promotion target changed independently; no automatic rollback")
    experiment.status = "active"
    experiment.selected_candidate = None
    _save(root, state, "promotion-rolled-back")


def promote_candidate(
    root: Path,
    config: HarnessConfig,
    task_id: str,
    candidate_id: str,
) -> ImprovementState:
    with task_lock(root, task_id), configuration_lock(root):
        state = load_task(root, task_id)
        experiment = _get(state)
        if experiment.status == "promoted" and experiment.selected_candidate == candidate_id:
            _fresh(root, config, experiment)
            target = _target(root, config, experiment.target)
            if _digest(_read_text(target)) != experiment.candidates[candidate_id].sha256:
                raise VerificationFailedError("promoted target changed")
            _current_attempt(root, experiment, "baseline")
            _current_attempt(root, experiment, candidate_id, require_passed=True)
            return experiment
        _ready(root, config, state)
        baseline_attempt = _current_attempt(root, experiment, "baseline")
        candidate_attempt = _current_attempt(root, experiment, candidate_id)
        assert baseline_attempt.score is not None and candidate_attempt.score is not None
        gain = candidate_attempt.score - baseline_attempt.score
        if (
            candidate_attempt.status != "passed"
            or gain <= 0
            or gain < experiment.specification.min_gain
        ):
            raise VerificationFailedError(
                "candidate failed hard checks or did not improve the baseline"
            )
        target = _target(root, config, experiment.target)
        if _digest(_read_text(target)) != experiment.candidates["baseline"].sha256:
            raise PolicyBlockedError("target changed since baseline; refusing to overwrite it")
        if not any(sensor.required for sensor in config.sensors):
            raise VerificationFailedError("promotion requires independent required project sensors")
        record_write_observation(root, task_id, target)
        state = load_task(root, task_id)
        experiment = _get(state)
        experiment.status, experiment.selected_candidate = "promoting", candidate_id
        _save(root, state, "promotion-prepared", candidate_id=candidate_id)
        required_failures = 0
        try:
            atomic_write_text(target, experiment.candidates[candidate_id].text)
            results = run_sensors(root, config, task_id=task_id)
            state = record_sensor_results(root, task_id, results)
            experiment = _get(state)
            passed = {result.name for result in results if result.passed}
            required_failures = sum(
                sensor.required and sensor.name not in passed for sensor in config.sensors
            )
            _fresh(root, config, experiment)
            if (
                not required_sensors_passed(config, results)
                or not required_sensor_evidence_is_current(root, config, state)
                or HarnessPaths(root).stop_file.exists()
                or HarnessPaths(root).improvement_stop_file(task_id).exists()
                or _digest(_read_text(target)) != experiment.candidates[candidate_id].sha256
            ):
                raise VerificationFailedError(
                    "promotion sensors failed, stopped, or changed the target"
                )
            _current_attempt(root, experiment, "baseline")
            _current_attempt(root, experiment, candidate_id, require_passed=True)
            experiment.status = "promoted"
            _save(root, state, "promoted", candidate_id=candidate_id)
            return experiment
        except Exception:
            _rollback(root, config, load_task(root, task_id))
            state = load_task(root, task_id)
            if (
                required_failures
                and active_trip_wires(
                    root, config, state, required_sensor_failures=required_failures
                )
                and state.status == TaskStatus.ACTIVE
            ):
                set_task_status(
                    root,
                    task_id,
                    TaskStatus.PAUSED,
                    error="improvement promotion required sensors failed",
                )
            raise


def cancel_improvement(root: Path, task_id: str) -> None:
    """Signal without acquiring the task lock held by a running sensor."""
    experiment = _get(load_task(root, task_id))
    if experiment.status not in {"active", "promoting", "conflict"}:
        raise PolicyBlockedError("only an unresolved improvement can be cancelled")
    atomic_write_text(HarnessPaths(root).improvement_stop_file(task_id), "cancelled\n")


def resume_improvement(
    root: Path,
    config: HarnessConfig,
    task_id: str,
    *,
    abandon: bool = False,
) -> ImprovementState:
    with task_lock(root, task_id), configuration_lock(root):
        state = load_task(root, task_id)
        experiment = _get(state)
        if state.status != TaskStatus.ACTIVE:
            raise PolicyBlockedError(
                "reactivate the original task first; terminal tasks cannot resume"
            )
        if experiment.status in {"promoting", "conflict"}:
            _rollback(root, config, state)
        if experiment.status == "promoted":
            raise PolicyBlockedError("a promoted experiment cannot be restarted")
        for attempt in experiment.attempts.values():
            if attempt.status == "running":
                attempt.status = "interrupted"
        experiment.status = "cancelled" if abandon else "active"
        _save(root, state, "abandoned" if abandon else "resumed")
        HarnessPaths(root).improvement_stop_file(task_id).unlink(missing_ok=True)
        return experiment


def ensure_improvement_resolved(root: Path, config: HarnessConfig, state: TaskState) -> None:
    if state.improvement is None:
        return
    experiment = state.improvement
    if experiment.status not in {"cancelled", "promoted"}:
        raise PolicyBlockedError("resolve or abandon the improvement experiment before completing")
    if any(request.status in {"pending", "unknown"} for request in experiment.requests.values()):
        raise PolicyBlockedError("reconcile unknown host usage before completing")
    if experiment.status == "promoted":
        _fresh(root, config, experiment)
        selected = experiment.candidates[experiment.selected_candidate or "baseline"]
        if _digest(_read_text(_target(root, config, experiment.target))) != selected.sha256:
            raise VerificationFailedError("promoted target changed before completion")
        _current_attempt(root, experiment, "baseline")
        _current_attempt(root, experiment, selected.candidate_id, require_passed=True)
