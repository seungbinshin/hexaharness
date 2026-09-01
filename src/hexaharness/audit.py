from __future__ import annotations

from pathlib import Path

from hexaharness.config import load_config
from hexaharness.learning import load_guide_rules
from hexaharness.models import AuditCheck, AuditStatus, TaskStatus
from hexaharness.paths import HarnessPaths
from hexaharness.state import list_tasks


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


def audit_harness(project_root: Path) -> list[AuditCheck]:
    config = load_config(project_root)
    paths = HarnessPaths(project_root)
    rules = [rule for rule in load_guide_rules(project_root) if rule.active]
    tasks = list_tasks(project_root)
    unattended_successes = sum(
        1 for task in tasks if task.unattended and task.status == TaskStatus.COMPLETED
    )
    required_sensors = [sensor for sensor in config.sensors if sensor.required]
    trip_metrics = {item.metric for item in config.trip_wires}
    event_count = len(list(paths.events.glob("*.jsonl"))) if paths.events.is_dir() else 0

    commands_present = all((config.project.build, config.project.test, config.project.lint))
    checks = [
        _check(
            1,
            "Guide file with exact build, test, and lint commands",
            (project_root / "AGENTS.md").is_file() and commands_present,
            f"AGENTS.md={'present' if (project_root / 'AGENTS.md').is_file() else 'missing'}; "
            f"project={config.project.name}",
            "Create AGENTS.md and configure exact command arrays in harness.yaml.",
        ),
        _check(
            2,
            "At least five guide rules traced to observed failures",
            len(rules) >= 5,
            f"{len(rules)} active failure-derived rules",
            "Use `hexa learn` after real failures; do not invent evidence.",
            evidence_gap=True,
        ),
        _check(
            3,
            "At least one required computational sensor",
            bool(required_sensors),
            f"required sensors: {', '.join(item.name for item in required_sensors) or 'none'}",
            "Configure a deterministic test, linter, type checker, build, or schema validator.",
        ),
        _check(
            4,
            "Bounded retry and escalation policy",
            config.budgets.max_retries_per_step >= 0
            and config.budgets.max_wall_time_seconds > 0
            and config.budgets.max_tool_calls > 0,
            f"retries={config.budgets.max_retries_per_step}, "
            f"wall={config.budgets.max_wall_time_seconds}s, "
            f"tools={config.budgets.max_tool_calls}",
            "Set finite retry, wall-time, and tool-call budgets.",
        ),
        _check(
            5,
            "Filesystem checkpoint and recovery state",
            paths.states.is_dir(),
            f"state directory: {paths.states.relative_to(project_root)}",
            "Run `hexa start` or recreate the runtime state directory.",
        ),
        _check(
            6,
            "Permission boundary with allow, ask, and deny decisions",
            bool(config.policy.allow_execute)
            and bool(config.policy.ask_execute)
            and bool(config.policy.deny_execute),
            f"allow={len(config.policy.allow_execute)}, ask={len(config.policy.ask_execute)}, "
            f"deny={len(config.policy.deny_execute)} command prefixes",
            "Define least-privilege allow, ask, and deny command prefixes.",
        ),
        _check(
            7,
            "Token and cost budgets",
            config.budgets.max_tokens > 0 and config.budgets.max_cost_usd >= 0,
            f"tokens={config.budgets.max_tokens}, cost=${config.budgets.max_cost_usd:.2f}",
            "Set finite token and cost budgets, even if the host reports zero usage.",
        ),
        _check(
            8,
            "Structured event logging",
            paths.events.is_dir() and any(paths.events.glob("*.jsonl")),
            f"event files: {event_count}",
            "Run a HexaHarness command that records an event and retain the JSONL evidence.",
        ),
        _check(
            9,
            "Trip wires for repeated errors and aggregate budgets",
            {"same_error_count", "tool_calls", "cost_usd"}.issubset(trip_metrics),
            f"configured metrics: {', '.join(sorted(trip_metrics))}",
            "Configure repeated-error, tool-call, and cost trip wires.",
        ),
        _check(
            10,
            "Trusted and untrusted input split",
            bool(config.trust.trusted_instructions) and bool(config.trust.untrusted_inputs),
            f"trusted={len(config.trust.trusted_instructions)}, "
            f"untrusted={len(config.trust.untrusted_inputs)} classes",
            "Declare trusted instruction sources and untrusted data classes.",
        ),
        _check(
            11,
            "Emergency stop with state preservation",
            config.emergency_stop_file == ".hexaharness/STOP",
            f"stop file: {config.emergency_stop_file}",
            "Configure the project-local emergency stop file.",
        ),
        _check(
            12,
            "Three successful unattended runs",
            unattended_successes >= 3,
            f"{unattended_successes} completed unattended runs",
            "Complete three real unattended tasks before enabling unattended production use.",
            evidence_gap=True,
        ),
    ]
    return checks


def audit_summary(checks: list[AuditCheck]) -> dict[str, int]:
    return {
        status.value: sum(1 for check in checks if check.status == status) for status in AuditStatus
    }
