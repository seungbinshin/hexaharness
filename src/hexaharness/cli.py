from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Annotated, Any

import typer

from hexaharness import __version__
from hexaharness.audit import audit_harness, audit_summary
from hexaharness.config import (
    CURRENT_SCHEMA_VERSION,
    configuration_issues,
    ensure_configuration_ready,
    load_config,
    load_config_snapshot,
)
from hexaharness.errors import HexaHarnessError, PolicyBlockedError, VerificationFailedError
from hexaharness.events import record_event
from hexaharness.io import redact_argv, redact_text_using_argv
from hexaharness.learning import record_learning
from hexaharness.models import (
    AuditStatus,
    FailureClass,
    HarnessLayer,
    PolicyDecision,
    TaskKind,
)
from hexaharness.paths import find_project_root
from hexaharness.policy import evaluate_command, evaluate_path
from hexaharness.retention import apply_prune, plan_prune
from hexaharness.runner import escalate_task, new_external_action_step, run_task_command
from hexaharness.scaffold import initialize_project
from hexaharness.sensors import run_sensors
from hexaharness.state import (
    checkpoint_task,
    list_tasks,
    load_task,
    record_sensor_results,
    record_write_observation,
    stage_external_action,
    start_task,
    task_lock,
)
from hexaharness.tripwires import active_trip_wires
from hexaharness.workflow import (
    complete_task,
    resume_task,
    stop_task_or_harness,
)

app = typer.Typer(
    name="hexa",
    help="Build, verify, and evolve a six-layer production agent harness.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

RootOption = Annotated[
    Path | None,
    typer.Option("--root", help="Project root. Defaults to the nearest initialized harness."),
]


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def app_callback(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Print the installed HexaHarness version and exit.",
        ),
    ] = False,
) -> None:
    """Apply deterministic controls to host-agent workflows."""


def _project_root(root: Path | None) -> Path:
    return find_project_root(root or Path.cwd())


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _emit_json(value: Any) -> None:
    typer.echo(json.dumps(_json_value(value), indent=2, sort_keys=True))


def _parse_override(value: str | None) -> list[str] | None:
    if value is None:
        return None
    parsed = shlex.split(value)
    if not parsed:
        raise typer.BadParameter("command overrides cannot be empty")
    return parsed


@app.command()
def version() -> None:
    """Print the installed HexaHarness version."""
    typer.echo(__version__)


@app.command("init")
def init_command(
    root: RootOption = None,
    project_name: Annotated[str | None, typer.Option("--project-name")] = None,
    language: Annotated[str | None, typer.Option(help="Project language or stack.")] = None,
    build: Annotated[str | None, typer.Option(help="Exact build command.")] = None,
    test: Annotated[str | None, typer.Option(help="Exact test command.")] = None,
    lint: Annotated[str | None, typer.Option(help="Exact lint command.")] = None,
    typecheck: Annotated[str | None, typer.Option(help="Exact type-check command.")] = None,
    force: Annotated[
        bool, typer.Option(help="Replace harness config, preserving existing agent guides.")
    ] = False,
) -> None:
    """Initialize the six-layer harness in a project."""
    project_root = (root or Path.cwd()).resolve()
    overrides = {
        "build": _parse_override(build),
        "test": _parse_override(test),
        "lint": _parse_override(lint),
        "typecheck": _parse_override(typecheck),
    }
    config = initialize_project(
        project_root,
        project_name=project_name,
        project_language=language,
        build=overrides["build"],
        test=overrides["test"],
        lint=overrides["lint"],
        typecheck=overrides["typecheck"],
        force=force,
    )
    _emit_json(
        {
            "project_root": str(project_root),
            "config": ".hexaharness/harness.yaml",
            "project": config.project,
        }
    )


@app.command()
def doctor(root: RootOption = None) -> None:
    """Validate and summarize the installed configuration."""
    project_root = _project_root(root)
    schema_version, config = load_config_snapshot(project_root)
    if config is None:
        _emit_json(
            {
                "status": "needs-configuration",
                "project_root": str(project_root),
                "schema_version": schema_version,
                "sensors": [],
                "budgets": None,
                "issues": [
                    f"schema_version {schema_version} is newer than supported "
                    f"{CURRENT_SCHEMA_VERSION}"
                ],
                "next_action": (
                    "upgrade the HexaHarness runtime before reading or changing this project"
                ),
            }
        )
        raise typer.Exit(2)
    issues = configuration_issues(config, project_root)
    _emit_json(
        {
            "status": "ok" if not issues else "needs-configuration",
            "project_root": str(project_root),
            "schema_version": config.schema_version,
            "sensors": [sensor.name for sensor in config.sensors],
            "budgets": config.budgets,
            "issues": issues,
            "next_action": (
                None
                if not issues
                else (
                    "upgrade the HexaHarness runtime before reading or changing this project"
                    if config.schema_version > CURRENT_SCHEMA_VERSION
                    else "run init --force after the stack and exact commands are available"
                )
            ),
        }
    )
    if issues:
        raise typer.Exit(2)


@app.command()
def start(
    goal: Annotated[str, typer.Argument(help="Observable task goal.")],
    root: RootOption = None,
    unattended: Annotated[bool, typer.Option(help="Mark the run as unattended evidence.")] = False,
    kind: Annotated[
        TaskKind, typer.Option(help="Expected outcome: change, review, or release.")
    ] = TaskKind.CHANGE,
) -> None:
    """Create a recoverable task checkpoint."""
    project_root = _project_root(root)
    ensure_configuration_ready(load_config(project_root), project_root)
    _emit_json(start_task(project_root, goal, unattended=unattended, kind=kind))


@app.command()
def checkpoint(
    task_id: Annotated[str, typer.Argument()],
    root: RootOption = None,
    completed: Annotated[str | None, typer.Option(help="Completed step to record.")] = None,
    next_step: Annotated[str | None, typer.Option("--next", help="Next pending step.")] = None,
    clear_next: Annotated[
        bool,
        typer.Option(
            "--clear-next",
            help="Clear the pending step after recording any required verification.",
        ),
    ] = False,
    resolve_external_action: Annotated[
        bool,
        typer.Option(
            "--resolve-external-action",
            help="Resolve a crash-recovery checkpoint with a completed step and evidence.",
        ),
    ] = False,
    cancel_external_action: Annotated[
        bool,
        typer.Option(
            "--cancel-external-action",
            help="Cancel an unexecuted pending external action and record the reason.",
        ),
    ] = False,
    artifact: Annotated[
        list[str] | None, typer.Option(help="Artifact path; repeat for multiple paths.")
    ] = None,
    tokens: Annotated[int, typer.Option(min=0)] = 0,
    cost_usd: Annotated[float, typer.Option("--cost-usd", min=0)] = 0.0,
) -> None:
    """Persist meaningful progress, artifacts, and host-reported usage."""
    project_root = _project_root(root)
    config = load_config(project_root)
    with task_lock(project_root, task_id):
        state = checkpoint_task(
            project_root,
            task_id,
            completed_step=completed,
            next_step=next_step,
            clear_next=clear_next,
            resolve_external_action=resolve_external_action,
            cancel_external_action=cancel_external_action,
            artifacts=artifact,
            tokens=tokens,
            cost_usd=cost_usd,
        )
        fired = active_trip_wires(project_root, config, state)
        if fired:
            state, _ = escalate_task(
                project_root,
                task_id,
                reason=f"task budget trip wire fired: {', '.join(fired)}",
                evidence_paths=state.artifacts,
                alternatives_tested=["the configured task budget was preserved"],
                cost_of_waiting="Further work is blocked until the budget or scope is reviewed.",
            )
    _emit_json(state)


@app.command(
    "prepare-external", context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def prepare_external(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument()],
    root: RootOption = None,
) -> None:
    """Durably stage one exact human-gated action before requesting approval."""
    argv = list(ctx.args)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        raise typer.BadParameter("provide an external command after `--`")
    project_root = _project_root(root)
    config = load_config(project_root)
    outcome = evaluate_command(config, argv, project_root=project_root)
    if outcome.decision == PolicyDecision.DENY:
        raise PolicyBlockedError(
            f"external action is denied: {redact_text_using_argv(outcome.reason, argv)}"
        )
    if outcome.decision != PolicyDecision.ASK or not outcome.requires_human_approval:
        raise PolicyBlockedError(
            "prepare-external only accepts an ask-gated action that requires human approval"
        )
    with task_lock(project_root, task_id):
        pending_step = new_external_action_step(project_root, task_id, argv)
        state = stage_external_action(project_root, task_id, pending_step=pending_step)
    redacted = redact_argv(argv)
    record_event(
        project_root,
        "external-action.pending",
        task_id=task_id,
        payload={"argv": redacted, "next_step": pending_step},
    )
    _emit_json(
        {
            "task_id": state.task_id,
            "status": state.status,
            "argv": redacted,
            "approval_required": True,
            "next_step": pending_step,
        }
    )


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument()],
    root: RootOption = None,
    approved: Annotated[
        bool,
        typer.Option(help="Assert that a human approved this ask-gated command."),
    ] = False,
    reviewed: Annotated[
        bool,
        typer.Option(
            help="Assert that the agent inspected this unregistered local command and its scope."
        ),
    ] = False,
    retries: Annotated[int, typer.Option(min=0)] = 0,
    timeout: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    """Execute one policy-gated command without a shell; pass it after `--`."""
    argv = list(ctx.args)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        raise typer.BadParameter("provide a command after `--`")
    project_root = _project_root(root)
    config = load_config(project_root)
    result = run_task_command(
        project_root,
        config,
        task_id=task_id,
        argv=argv,
        approved=approved,
        reviewed=reviewed,
        retries=retries,
        timeout_seconds=timeout,
    )
    _emit_json(result)
    if result.exit_code != 0 or result.timed_out or result.stopped:
        raise typer.Exit(1)


@app.command()
def verify(
    task_id: Annotated[str | None, typer.Argument()] = None,
    root: RootOption = None,
    sensor: Annotated[
        list[str] | None, typer.Option(help="Sensor name; repeat to select multiple.")
    ] = None,
) -> None:
    """Run configured computational sensors and retain their evidence."""
    project_root = _project_root(root)
    config = load_config(project_root)
    if task_id:
        with task_lock(project_root, task_id):
            load_task(project_root, task_id)
            results = run_sensors(project_root, config, task_id=task_id, names=sensor)
            record_sensor_results(project_root, task_id, results)
    else:
        results = run_sensors(project_root, config, task_id=None, names=sensor)
    _emit_json(results)
    if any(not result.passed for result in results):
        raise typer.Exit(1)


@app.command()
def complete(
    task_id: Annotated[str, typer.Argument()],
    root: RootOption = None,
    artifact: Annotated[list[str] | None, typer.Option()] = None,
) -> None:
    """Verify required sensors and mark a task complete only when they pass."""
    project_root = _project_root(root)
    config = load_config(project_root)
    try:
        state = complete_task(project_root, config, task_id, artifacts=artifact)
    except VerificationFailedError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    _emit_json(state)


@app.command()
def resume(
    task_id: Annotated[str, typer.Argument()],
    root: RootOption = None,
    clear_stop: Annotated[
        bool, typer.Option(help="Clear the project emergency-stop file.")
    ] = False,
    reactivate: Annotated[
        bool, typer.Option(help="Reactivate a paused or escalated task after review.")
    ] = False,
) -> None:
    """Read a checkpoint and optionally reactivate it after review."""
    project_root = _project_root(root)
    load_config(project_root)
    _emit_json(
        resume_task(
            project_root,
            task_id,
            clear_stop=clear_stop,
            reactivate=reactivate,
        )
    )


@app.command()
def learn(
    failure_class: Annotated[FailureClass, typer.Option("--class")],
    summary: Annotated[str, typer.Option(help="Observed failure, not a hypothetical one.")],
    fix: Annotated[str, typer.Option(help="Proposed structural correction.")],
    verification: Annotated[str, typer.Option(help="How recurrence will be tested.")],
    task_id: Annotated[str, typer.Option(help="Task that observed the failure.")],
    evidence: Annotated[
        list[str],
        typer.Option(help="Existing evidence artifact; repeat for multiple paths."),
    ],
    root: RootOption = None,
    layer: Annotated[HarnessLayer | None, typer.Option()] = None,
    guide_rule: Annotated[
        str | None,
        typer.Option(help="Apply a dated guide rule when the chosen layer is guide."),
    ] = None,
) -> None:
    """Convert an observed failure into a traceable harness improvement."""
    project_root = _project_root(root)
    load_config(project_root)
    record = record_learning(
        project_root,
        failure_class=failure_class,
        failure_summary=summary,
        proposed_fix=fix,
        verification=verification,
        task_id=task_id,
        evidence_paths=evidence,
        target_layer=layer,
        guide_rule=guide_rule,
    )
    record_event(
        project_root,
        "learning.recorded",
        task_id=task_id,
        payload={
            "learning_id": record.learning_id,
            "failure_class": record.failure_class.value,
            "target_layer": record.target_layer.value,
            "guide_rule_id": record.guide_rule_id,
            "evidence_paths": record.evidence_paths,
        },
    )
    _emit_json(record)


@app.command()
def audit(
    root: RootOption = None,
    strict: Annotated[
        bool, typer.Option(help="Treat evidence-gathering warnings as failures.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Evaluate the twelve-item production-readiness checklist."""
    project_root = _project_root(root)
    checks = audit_harness(project_root)
    summary = audit_summary(checks)
    if json_output:
        _emit_json({"summary": summary, "checks": checks})
    else:
        for check in checks:
            typer.echo(
                f"{check.number:>2}. {check.status.value.upper():4} "
                f"{check.requirement} — {check.evidence}"
            )
            if check.remedy:
                typer.echo(f"    next: {check.remedy}")
        typer.echo(
            f"summary: {summary['pass']} pass, {summary['warn']} warn, {summary['fail']} fail"
        )
    failed = summary[AuditStatus.FAIL.value] > 0
    warned = summary[AuditStatus.WARN.value] > 0
    if failed or (strict and warned):
        raise typer.Exit(1)


@app.command()
def status(
    task_id: Annotated[str | None, typer.Argument()] = None,
    root: RootOption = None,
) -> None:
    """Show one task checkpoint or the recent task ledger."""
    project_root = _project_root(root)
    load_config(project_root)
    value: Any = load_task(project_root, task_id) if task_id else list_tasks(project_root)
    _emit_json(value)


@app.command()
def stop(
    root: RootOption = None,
    task_id: Annotated[str | None, typer.Argument()] = None,
    reason: Annotated[str, typer.Option(help="Why execution must stop.")] = "operator stop",
) -> None:
    """Stop one task or activate the project-wide emergency stop."""
    project_root = _project_root(root)
    load_config(project_root)
    state = stop_task_or_harness(project_root, task_id=task_id, reason=reason)
    _emit_json(
        state
        if state is not None
        else {"status": "stopped", "stop_file": ".hexaharness/STOP", "reason": reason}
    )


@app.command()
def prune(
    root: RootOption = None,
    apply: Annotated[
        bool, typer.Option("--apply", help="Delete the listed expired runtime files.")
    ] = False,
) -> None:
    """Plan or apply retention cleanup for terminal task evidence."""
    project_root = _project_root(root)
    config = load_config(project_root)
    candidates = plan_prune(project_root, config)
    relative = [path.relative_to(project_root).as_posix() for path in candidates]
    deleted = apply_prune(project_root, candidates) if apply else []
    if apply:
        record_event(
            project_root,
            "retention.pruned",
            payload={"deleted_count": len(deleted)},
        )
    _emit_json(
        {
            "mode": "apply" if apply else "dry-run",
            "candidates": relative,
            "deleted": deleted,
        }
    )


@app.command(
    "policy-check",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def policy_check(
    ctx: typer.Context,
    root: RootOption = None,
    task_id: Annotated[
        str | None,
        typer.Option(help="Associate the decision evidence with an existing task."),
    ] = None,
    path: Annotated[
        Path | None, typer.Option(help="Path to evaluate instead of a command.")
    ] = None,
    write: Annotated[bool, typer.Option(help="Evaluate a write rather than a read.")] = False,
) -> None:
    """Evaluate a command or path without performing the action."""
    project_root = _project_root(root)
    config = load_config(project_root)
    if task_id is not None:
        load_task(project_root, task_id)
    if path is not None:
        outcome = evaluate_path(config, project_root, path, write=write)
        policy_reason = outcome.reason
        subject: Any = {"path": str(path), "write": write}
        event_type = "policy.path"
        candidate = path if path.is_absolute() else project_root / path
        try:
            canonical_path = candidate.resolve().relative_to(project_root).as_posix()
        except ValueError:
            canonical_path = "<outside-project>"
        event_subject: dict[str, Any] = {"path": canonical_path, "write": write}
    else:
        argv = list(ctx.args)
        if argv and argv[0] == "--":
            argv = argv[1:]
        if not argv:
            raise typer.BadParameter("provide --path or a command after `--`")
        outcome = evaluate_command(config, argv, project_root=project_root)
        policy_reason = redact_text_using_argv(outcome.reason, argv)
        redacted = redact_argv(argv)
        subject = {"argv": redacted}
        event_type = "policy.command-check"
        event_subject = {"argv": redacted}
    authorization = None
    if outcome.decision == PolicyDecision.ASK:
        authorization = "human-approval" if outcome.requires_human_approval else "agent-review"
    record_event(
        project_root,
        event_type,
        task_id=task_id,
        payload={
            **event_subject,
            "decision": outcome.decision.value,
            "reason": policy_reason,
            "authorization": authorization,
        },
    )
    if (
        path is not None
        and write
        and task_id is not None
        and outcome.decision == PolicyDecision.ALLOW
    ):
        observation = record_write_observation(project_root, task_id, path)
        subject["write_observation_id"] = observation.observation_id
    _emit_json(
        {
            **subject,
            "decision": outcome.decision.value,
            "authorization": authorization,
            "reason": policy_reason,
        }
    )
    if outcome.decision == PolicyDecision.DENY:
        raise typer.Exit(2)
    if outcome.decision == PolicyDecision.ASK:
        raise typer.Exit(3)


def main() -> None:
    try:
        app()
    except (HexaHarnessError, FileNotFoundError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise SystemExit(2) from error
