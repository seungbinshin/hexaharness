from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Annotated, Any

import typer

from hexaharness import __version__
from hexaharness.audit import audit_harness, audit_summary
from hexaharness.config import (
    default_policy,
    default_sensors,
    load_config,
    save_config,
)
from hexaharness.errors import HexaHarnessError, VerificationFailedError
from hexaharness.events import record_event
from hexaharness.learning import record_learning
from hexaharness.models import (
    AuditStatus,
    FailureClass,
    HarnessLayer,
    PolicyDecision,
    TaskStatus,
)
from hexaharness.paths import find_project_root
from hexaharness.policy import evaluate_command, evaluate_path
from hexaharness.retention import apply_prune, plan_prune
from hexaharness.runner import run_task_command
from hexaharness.scaffold import initialize_project
from hexaharness.sensors import run_sensors
from hexaharness.state import (
    checkpoint_task,
    list_tasks,
    load_task,
    record_sensor_results,
    save_task,
    start_task,
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
    config = initialize_project(project_root, project_name=project_name, force=force)
    overrides = {
        "build": _parse_override(build),
        "test": _parse_override(test),
        "lint": _parse_override(lint),
        "typecheck": _parse_override(typecheck),
    }
    changed = False
    for field, command in overrides.items():
        if command is not None:
            setattr(config.project, field, command)
            changed = True
    if changed:
        config.sensors = default_sensors(config.project)
        config.policy = default_policy(config.project)
        save_config(project_root, config)
        record_event(project_root, "harness.commands-overridden")
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
    config = load_config(project_root)
    _emit_json(
        {
            "status": "ok",
            "project_root": str(project_root),
            "schema_version": config.schema_version,
            "sensors": [sensor.name for sensor in config.sensors],
            "budgets": config.budgets,
        }
    )


@app.command()
def start(
    goal: Annotated[str, typer.Argument(help="Observable task goal.")],
    root: RootOption = None,
    unattended: Annotated[bool, typer.Option(help="Mark the run as unattended evidence.")] = False,
) -> None:
    """Create a recoverable task checkpoint."""
    project_root = _project_root(root)
    load_config(project_root)
    _emit_json(start_task(project_root, goal, unattended=unattended))


@app.command()
def checkpoint(
    task_id: Annotated[str, typer.Argument()],
    root: RootOption = None,
    completed: Annotated[str | None, typer.Option(help="Completed step to record.")] = None,
    next_step: Annotated[str | None, typer.Option("--next", help="Next pending step.")] = None,
    artifact: Annotated[
        list[str] | None, typer.Option(help="Artifact path; repeat for multiple paths.")
    ] = None,
    tokens: Annotated[int, typer.Option(min=0)] = 0,
    cost_usd: Annotated[float, typer.Option("--cost-usd", min=0)] = 0.0,
) -> None:
    """Persist meaningful progress, artifacts, and host-reported usage."""
    project_root = _project_root(root)
    config = load_config(project_root)
    state = checkpoint_task(
        project_root,
        task_id,
        completed_step=completed,
        next_step=next_step,
        artifacts=artifact,
        tokens=tokens,
        cost_usd=cost_usd,
    )
    fired = active_trip_wires(project_root, config, state)
    if fired:
        state.status = TaskStatus.PAUSED
        save_task(project_root, state)
    _emit_json(state)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument()],
    root: RootOption = None,
    approved: Annotated[
        bool,
        typer.Option(help="Assert that a human approved this ask-gated command."),
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
        retries=retries,
        timeout_seconds=timeout,
    )
    _emit_json(result)
    if result.exit_code != 0 or result.timed_out:
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
        load_task(project_root, task_id)
    results = run_sensors(project_root, config, task_id=task_id, names=sensor)
    if task_id:
        record_sensor_results(project_root, task_id, results)
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
    root: RootOption = None,
    task_id: Annotated[str | None, typer.Option()] = None,
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
    path: Annotated[
        Path | None, typer.Option(help="Path to evaluate instead of a command.")
    ] = None,
    write: Annotated[bool, typer.Option(help="Evaluate a write rather than a read.")] = False,
) -> None:
    """Evaluate a command or path without performing the action."""
    project_root = _project_root(root)
    config = load_config(project_root)
    if path is not None:
        outcome = evaluate_path(config, project_root, path, write=write)
        subject: Any = {"path": str(path), "write": write}
    else:
        argv = list(ctx.args)
        if argv and argv[0] == "--":
            argv = argv[1:]
        if not argv:
            raise typer.BadParameter("provide --path or a command after `--`")
        outcome = evaluate_command(config, argv)
        subject = {"argv": argv}
    _emit_json({**subject, "decision": outcome.decision.value, "reason": outcome.reason})
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
