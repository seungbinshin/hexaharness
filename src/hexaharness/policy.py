from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

from hexaharness.models import HarnessConfig, PolicyDecision


@dataclass(frozen=True)
class PolicyOutcome:
    decision: PolicyDecision
    reason: str


def _matches_prefix(argv: list[str], prefix: list[str]) -> bool:
    return len(argv) >= len(prefix) and argv[: len(prefix)] == prefix


def evaluate_command(config: HarnessConfig, argv: list[str]) -> PolicyOutcome:
    if not argv:
        return PolicyOutcome(PolicyDecision.DENY, "empty commands are invalid")
    for prefix in config.policy.deny_execute:
        if _matches_prefix(argv, prefix):
            return PolicyOutcome(PolicyDecision.DENY, f"matches deny prefix: {' '.join(prefix)}")
    for prefix in config.policy.ask_execute:
        if _matches_prefix(argv, prefix):
            return PolicyOutcome(PolicyDecision.ASK, f"matches approval prefix: {' '.join(prefix)}")
    for prefix in config.policy.allow_execute:
        if _matches_prefix(argv, prefix):
            return PolicyOutcome(PolicyDecision.ALLOW, f"matches allow prefix: {' '.join(prefix)}")
    return PolicyOutcome(config.policy.unknown_execute, "no configured command prefix matched")


def _matches_path(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def evaluate_path(
    config: HarnessConfig,
    project_root: Path,
    target: Path,
    *,
    write: bool,
) -> PolicyOutcome:
    root = project_root.resolve()
    resolved = target.resolve() if target.is_absolute() else (root / target).resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        return PolicyOutcome(PolicyDecision.DENY, "target escapes the configured project root")

    if write and _matches_path(relative, config.policy.deny_write_paths):
        return PolicyOutcome(PolicyDecision.DENY, "target matches a denied write path")
    if write and _matches_path(relative, config.policy.write_paths):
        return PolicyOutcome(PolicyDecision.ALLOW, "target matches an allowed write path")
    if write and _matches_path(relative, config.policy.ask_write_paths):
        return PolicyOutcome(PolicyDecision.ASK, "target requires write approval")
    if not write and _matches_path(relative, config.policy.read_paths):
        return PolicyOutcome(PolicyDecision.ALLOW, "target matches an allowed read path")
    return PolicyOutcome(PolicyDecision.ASK, "target is not covered by an explicit path rule")
