from __future__ import annotations

from pathlib import Path

from hexaharness.config import configuration_issues, is_placeholder_command, load_config
from hexaharness.events import iter_events
from hexaharness.learning import learning_provenance_is_current, load_guide_rules
from hexaharness.models import AuditCheck, AuditStatus, HarnessConfig, TaskState, TaskStatus
from hexaharness.paths import HarnessPaths
from hexaharness.scaffold import GUIDE_BLOCK_END, GUIDE_BLOCK_START, render_managed_guide
from hexaharness.state import (
    fresh_completion_artifact_claims,
    list_tasks,
    qualifying_fresh_completion_artifacts,
    required_sensor_evidence_is_current,
)


def _check(
    number: int,
    requirement: str,
    condition: bool,
    evidence: str,
    remedy: str,
    *,
    evidence_gap: bool = False,
) -> AuditCheck:
    if condition:
        status = AuditStatus.PASS
        effective_remedy = None
    elif evidence_gap:
        status = AuditStatus.WARN
        effective_remedy = remedy
    else:
        status = AuditStatus.FAIL
        effective_remedy = remedy
    return AuditCheck(
        number=number,
        requirement=requirement,
        status=status,
        evidence=evidence,
        remedy=effective_remedy,
    )


def _evidence_check(
    number: int,
    requirement: str,
    *,
    structurally_ready: bool,
    observed: bool,
    evidence: str,
    structural_remedy: str,
    evidence_remedy: str,
) -> AuditCheck:
    """Fail missing controls, but warn when a configured control lacks operating evidence."""
    if not structurally_ready:
        return _check(
            number,
            requirement,
            False,
            evidence,
            structural_remedy,
        )
    return _check(
        number,
        requirement,
        observed,
        evidence,
        evidence_remedy,
        evidence_gap=True,
    )


def _required_sensor_evidence(
    project_root: Path,
    config: HarnessConfig,
    tasks: list[TaskState],
) -> set[str]:
    return {
        task.task_id
        for task in tasks
        if required_sensor_evidence_is_current(project_root, config, task)
    }


def _valid_unattended_successes(
    project_root: Path, config: HarnessConfig, tasks: list[TaskState]
) -> list[TaskState]:
    candidates = [
        task
        for task in tasks
        if task.unattended
        and task.status == TaskStatus.COMPLETED
        and qualifying_fresh_completion_artifacts(project_root, task)
        and required_sensor_evidence_is_current(project_root, config, task)
    ]
    successes: list[TaskState] = []
    used_claims: set[tuple[str, str]] = set()
    for task in sorted(candidates, key=lambda item: item.ended_at or item.updated_at):
        claims = fresh_completion_artifact_claims(project_root, task)
        if claims - used_claims:
            successes.append(task)
            used_claims.update(claims)
    return successes


def _managed_command_section(content: str) -> list[str] | None:
    """Extract the canonical command entries from one well-formed managed guide block."""
    if content.count(GUIDE_BLOCK_START) != 1 or content.count(GUIDE_BLOCK_END) != 1:
        return None
    start = content.index(GUIDE_BLOCK_START)
    end = content.index(GUIDE_BLOCK_END)
    if end < start:
        return None

    lines = content[start:end].splitlines()
    heading = "- Required commands:"
    if lines.count(heading) != 1:
        return None
    heading_index = lines.index(heading)
    commands: list[str] = []
    for line in lines[heading_index + 1 :]:
        if not line.startswith("  - "):
            break
        commands.append(line)
    return commands or None


def _guide_commands_are_current(content: str, config: HarnessConfig) -> bool:
    expected = _managed_command_section(render_managed_guide(config))
    return expected is not None and _managed_command_section(content) == expected


def audit_harness(project_root: Path) -> list[AuditCheck]:
    config = load_config(project_root)
    readiness_issues = configuration_issues(config, project_root)
    paths = HarnessPaths(project_root)
    rules = [rule for rule in load_guide_rules(project_root) if rule.active]
    tasks = list_tasks(project_root)
    tasks_by_id = {task.task_id: task for task in tasks}
    evidenced_rules = [
        rule
        for rule in rules
        if rule.task_id in tasks_by_id
        and learning_provenance_is_current(project_root, rule, tasks_by_id[rule.task_id])
    ]
    evidenced_rule_tasks = {rule.task_id for rule in evidenced_rules}
    events = list(iter_events(project_root))
    required_sensors = [
        sensor
        for sensor in config.sensors
        if sensor.required and not is_placeholder_command(sensor.argv)
    ]
    required_names = {sensor.name for sensor in required_sensors}
    sensor_evidence = _required_sensor_evidence(project_root, config, tasks)
    unattended_successes = _valid_unattended_successes(project_root, config, tasks)
    trip_metrics = {item.metric for item in config.trip_wires}
    event_count = len(list(paths.events.glob("*.jsonl"))) if paths.events.is_dir() else 0
    event_types = {event.event_type for event in events}

    agents_path = project_root / "AGENTS.md"
    agents_content = agents_path.read_text(encoding="utf-8") if agents_path.is_file() else ""
    managed_commands_current = _guide_commands_are_current(agents_content, config)
    commands_present = not readiness_issues and managed_commands_current
    checks = [
        _check(
            1,
            "Guide file with exact build, test, lint, and optional type-check commands",
            agents_path.is_file() and commands_present,
            f"AGENTS.md={'present' if agents_path.is_file() else 'missing'}; "
            f"project={config.project.name}; "
            "managed-commands="
            f"{'current' if managed_commands_current else 'missing-or-stale'}; "
            f"readiness={'; '.join(readiness_issues) if readiness_issues else 'configured'}",
            "Refresh the managed AGENTS.md command guidance from harness.yaml.",
        ),
        _check(
            2,
            "At least five guide rules traced to observed failures",
            len(evidenced_rules) >= 5 and len(evidenced_rule_tasks) >= 5,
            f"{len(evidenced_rules)} provenance-verified active rules from "
            f"{len(evidenced_rule_tasks)} distinct failure tasks; {len(rules)} total active rules",
            "Use `hexa learn` with a source task and retained evidence after real failures.",
            evidence_gap=True,
        ),
        _evidence_check(
            3,
            "Required computational sensors with execution evidence",
            structurally_ready=bool(required_sensors),
            observed=bool(sensor_evidence),
            evidence=(
                f"required sensors: {', '.join(sorted(required_names)) or 'none'}; "
                f"passing evidence sets: {len(sensor_evidence)}"
            ),
            structural_remedy=(
                "Configure a deterministic test, linter, type checker, build, or schema validator."
            ),
            evidence_remedy="Run all required sensors and retain their passing evidence.",
        ),
        _evidence_check(
            4,
            "Bounded retry and escalation policy with drill evidence",
            structurally_ready=(
                config.budgets.max_retries_per_step >= 0
                and config.budgets.max_wall_time_seconds > 0
                and config.budgets.max_tool_calls > 0
            ),
            observed="task.escalation-created" in event_types,
            evidence=(
                f"retries={config.budgets.max_retries_per_step}, "
                f"wall={config.budgets.max_wall_time_seconds}s, "
                f"tools={config.budgets.max_tool_calls}; "
                "escalation evidence="
                + ("present" if "task.escalation-created" in event_types else "absent")
            ),
            structural_remedy="Set finite retry, wall-time, and tool-call budgets.",
            evidence_remedy="Exercise a bounded failure and retain its escalation packet.",
        ),
        _evidence_check(
            5,
            "Filesystem checkpoint and recovery state with resume evidence",
            structurally_ready=paths.states.is_dir(),
            observed="task.resumed" in event_types,
            evidence=(
                f"state directory: {paths.states.relative_to(project_root)}; "
                f"resume evidence={'present' if 'task.resumed' in event_types else 'absent'}"
            ),
            structural_remedy="Run `hexa start` or recreate the runtime state directory.",
            evidence_remedy="Resume a real checkpoint and retain the recovery event.",
        ),
        _evidence_check(
            6,
            "Permission boundary with exercised ask or deny decision",
            structurally_ready=(
                bool(config.policy.allow_execute)
                and bool(config.policy.ask_execute)
                and bool(config.policy.deny_execute)
            ),
            observed=any(
                event.event_type in {"policy.command", "policy.command-check", "policy.path"}
                and event.payload.get("decision") in {"ask", "deny"}
                for event in events
            ),
            evidence=(
                f"allow={len(config.policy.allow_execute)}, ask={len(config.policy.ask_execute)}, "
                f"deny={len(config.policy.deny_execute)} command prefixes; "
                "blocked-boundary evidence="
                + (
                    "present"
                    if any(
                        event.event_type
                        in {"policy.command", "policy.command-check", "policy.path"}
                        and event.payload.get("decision") in {"ask", "deny"}
                        for event in events
                    )
                    else "absent"
                )
            ),
            structural_remedy="Define least-privilege allow, ask, and deny command prefixes.",
            evidence_remedy="Exercise an ask or deny boundary and retain the policy event.",
        ),
        _check(
            7,
            "Token and cost budgets",
            config.budgets.max_tokens > 0 and config.budgets.max_cost_usd >= 0,
            f"tokens={config.budgets.max_tokens}, cost=${config.budgets.max_cost_usd:.2f}",
            "Set finite token and cost budgets, even if the host reports zero usage.",
        ),
        _evidence_check(
            8,
            "Structured event logging with lifecycle and control evidence",
            structurally_ready=paths.events.is_dir() and bool(events),
            observed=(
                bool(event_types & {"task.started", "task.resumed", "task.completed"})
                and bool(
                    event_types
                    & {"sensor.completed", "policy.command", "policy.command-check", "policy.path"}
                )
            ),
            evidence=(
                f"event files: {event_count}; parsed events: {len(events)}; "
                f"event types: {', '.join(sorted(event_types)) or 'none'}"
            ),
            structural_remedy=(
                "Run a HexaHarness command that records an event and retain the JSONL evidence."
            ),
            evidence_remedy="Retain both task-lifecycle and sensor or policy-control events.",
        ),
        _evidence_check(
            9,
            "Trip wires for repeated errors and aggregate budgets with firing evidence",
            structurally_ready={"same_error_count", "tool_calls", "cost_usd"}.issubset(
                trip_metrics
            ),
            observed="trip-wire.fired" in event_types,
            evidence=(
                f"configured metrics: {', '.join(sorted(trip_metrics))}; "
                f"firing evidence={'present' if 'trip-wire.fired' in event_types else 'absent'}"
            ),
            structural_remedy="Configure repeated-error, tool-call, and cost trip wires.",
            evidence_remedy="Exercise a trip wire safely and retain the firing event.",
        ),
        _check(
            10,
            "Trusted and untrusted input split",
            bool(config.trust.trusted_instructions) and bool(config.trust.untrusted_inputs),
            f"trusted={len(config.trust.trusted_instructions)}, "
            f"untrusted={len(config.trust.untrusted_inputs)} classes",
            "Declare trusted instruction sources and untrusted data classes.",
        ),
        _evidence_check(
            11,
            "Emergency stop with state-preserving recovery evidence",
            structurally_ready=config.emergency_stop_file == ".hexaharness/STOP",
            observed={"harness.stopped", "harness.stop-cleared"}.issubset(event_types),
            evidence=(
                f"stop file: {config.emergency_stop_file}; stop drill="
                + (
                    "complete"
                    if {"harness.stopped", "harness.stop-cleared"}.issubset(event_types)
                    else "not yet observed"
                )
            ),
            structural_remedy="Configure the project-local emergency stop file.",
            evidence_remedy="Exercise stop and reviewed resume, then retain both events.",
        ),
        _check(
            12,
            "Three successful unattended runs",
            len(unattended_successes) >= 3,
            f"{len(unattended_successes)} evidence-complete unattended runs",
            "Complete three real unattended tasks before enabling unattended production use.",
            evidence_gap=True,
        ),
    ]
    return checks


def audit_summary(checks: list[AuditCheck]) -> dict[str, int]:
    return {
        status.value: sum(1 for check in checks if check.status == status) for status in AuditStatus
    }
