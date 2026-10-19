"""Bounded model handoff and receipt adapter; the host owns actual provider execution."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from pathlib import Path

from hexaharness.errors import PolicyBlockedError
from hexaharness.improvement import (
    _get,
    _host_evidence,
    _host_request_digest,
    _save,
    cancel_improvement,
    record_generation_usage,
    request_candidate,
    submit_candidate_to_state,
)
from hexaharness.improvement_models import (
    Candidate,
    HostAdapterSpec,
    HostCall,
    HostRequest,
    HostResult,
    HostTransfer,
)
from hexaharness.models import HarnessConfig, utc_now
from hexaharness.runner import execution_time_remaining
from hexaharness.state import configuration_lock, load_task, task_lock


def prepare_host_request(
    root: Path,
    config: HarnessConfig,
    task_id: str,
    *,
    key: str,
    adapter: HostAdapterSpec,
    goal: str,
    max_tokens: int,
    max_cost_usd: float,
    timeout_seconds: int = 60,
    parents: list[str] | None = None,
) -> HostTransfer:
    """Reserve once and return data only; never launch a model or expand host authority."""
    if not all(
        (
            adapter.token_limit_enforced,
            adapter.cost_limit_enforced,
            adapter.cancellation_supported,
            adapter.reconciliation_supported,
        )
    ):
        raise PolicyBlockedError(
            "host call limits, cancellation and reconciliation must be confirmed"
        )
    if not 1 <= timeout_seconds <= 3600:
        raise ValueError("host timeout must be between 1 and 3600 seconds")
    if max_tokens < 1 or not goal.strip() or len(goal) > 2000:
        raise ValueError("host requests require positive tokens and a bounded nonblank goal")
    with task_lock(root, task_id), configuration_lock(root):
        state = load_task(root, task_id)
        experiment = _get(state)
        if previous := experiment.requests.get(key):
            call = previous.host_call
            if (
                call is None
                or call.adapter != adapter
                or call.request.goal != goal
                or call.request.max_tokens != max_tokens
                or call.request.max_cost_usd != max_cost_usd
                or call.timeout_seconds != timeout_seconds
                or previous.parents != (parents or ["baseline"])
            ):
                raise PolicyBlockedError("idempotency key already belongs to another host intent")
            _host_evidence(experiment, previous)
            return HostTransfer(created=False, request=call.request)
        remaining = execution_time_remaining(root, config, state)
        request_candidate(
            root,
            config,
            task_id,
            key=key,
            max_tokens=max_tokens,
            max_cost_usd=max_cost_usd,
            parents=parents,
        )
        state = load_task(root, task_id)
        experiment = _get(state)
        request = experiment.requests[key]
        envelope = HostRequest(
            call_id=uuid.uuid4().hex,
            request_sha256="",
            adapter_id=adapter.adapter_id,
            model_id=adapter.model_id,
            goal=goal,
            parents={parent: experiment.candidates[parent].text for parent in request.parents},
            max_tokens=max_tokens,
            max_cost_usd=max_cost_usd,
            deadline_at=utc_now() + timedelta(seconds=min(timeout_seconds, remaining)),
        )
        if state.task_budget is not None:
            envelope.deadline_at = min(envelope.deadline_at, state.task_budget.deadline_at)
        envelope.request_sha256 = _host_request_digest(envelope)
        request.host_call = HostCall(
            request=envelope, adapter=adapter, timeout_seconds=timeout_seconds
        )
        _save(root, state, "host-requested", key=key, call_id=envelope.call_id)
        return HostTransfer(created=True, request=envelope)


def receive_host_result(
    root: Path, config: HarnessConfig, task_id: str, *, key: str, result: HostResult
) -> Candidate | None:
    """Reconcile a provider or labelled fixture receipt without redispatching the request."""
    with task_lock(root, task_id):
        state = load_task(root, task_id)
        experiment = _get(state)
        request = experiment.requests.get(key)
        if request is None or request.host_call is None:
            raise PolicyBlockedError("a bound host request is required before its receipt")
        _host_evidence(experiment, request)
        call = request.host_call
        envelope = call.request
        if (
            _host_request_digest(envelope) != envelope.request_sha256
            or result.call_id != envelope.call_id
            or result.request_sha256 != envelope.request_sha256
            or result.adapter_id != envelope.adapter_id
            or result.model_id != envelope.model_id
        ):
            raise PolicyBlockedError("host receipt identity does not match the reserved request")
        if call.provenance is not None and call.provenance != result.provenance:
            raise PolicyBlockedError("receipt provenance cannot be rewritten")
        if (
            call.provider_request_id is not None
            and call.provider_request_id != result.provider_request_id
        ):
            raise PolicyBlockedError("provider request identity cannot be rewritten")
        if call.status in {"completed", "cancelled", "failed"} and call.status != result.status:
            raise PolicyBlockedError("resolved host outcome cannot be rewritten")
        candidate = None
        if result.text is not None:
            candidate = submit_candidate_to_state(
                state, key=key, text=result.text, tokens=result.tokens, cost_usd=result.cost_usd
            )
        else:
            record_generation_usage(state, request, tokens=result.tokens, cost_usd=result.cost_usd)
            if result.status == "unknown":
                request.status = "unknown"
        call.status = result.status
        call.provenance = result.provenance
        call.provider_request_id = result.provider_request_id
        call.receipt_sha256 = hashlib.sha256(result.model_dump_json().encode()).hexdigest()
        _save(
            root,
            state,
            "host-reconciled",
            key=key,
            status=call.status,
            provenance=result.provenance,
        )
        return candidate


def request_host_cancellation(root: Path, task_id: str, *, key: str) -> HostRequest:
    """Signal local cancellation and return the exact remote call to cancel; no fake ACK."""
    preview = _get(load_task(root, task_id)).requests.get(key)
    if preview is None or preview.host_call is None:
        raise PolicyBlockedError("a bound host request is required for cancellation")
    # The lock-free signal can interrupt local work before waiting for the checkpoint lock.
    cancel_improvement(root, task_id)
    with task_lock(root, task_id):
        state = load_task(root, task_id)
        request = _get(state).requests.get(key)
        if request is None or request.host_call is None:
            raise PolicyBlockedError("a bound host request is required for cancellation")
        request.host_call.cancel_requested = True
        _save(root, state, "host-cancellation-requested", key=key)
        return request.host_call.request
