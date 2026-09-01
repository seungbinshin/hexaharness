from __future__ import annotations

from pathlib import Path

import yaml

from hexaharness.io import atomic_write_text
from hexaharness.models import (
    BudgetConfig,
    CommandSpec,
    HarnessConfig,
    PolicyConfig,
    ProjectConfig,
    RetentionConfig,
    TripWireSpec,
    TrustConfig,
)
from hexaharness.paths import HarnessPaths


def _deduplicate_commands(commands: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    result: list[list[str]] = []
    for command in commands:
        key = tuple(command)
        if key not in seen:
            seen.add(key)
            result.append(command)
    return result


def infer_project(project_root: Path, name: str | None = None) -> ProjectConfig:
    project_name = name or project_root.name
    if (project_root / "pyproject.toml").is_file():
        return ProjectConfig(
            name=project_name,
            language="Python",
            build=["uv", "build"],
            test=["uv", "run", "pytest"],
            lint=["uv", "run", "ruff", "check", "."],
            typecheck=["uv", "run", "mypy", "src"],
        )
    if (project_root / "package.json").is_file():
        return ProjectConfig(
            name=project_name,
            language="TypeScript/JavaScript",
            build=["npm", "run", "build"],
            test=["npm", "test"],
            lint=["npm", "run", "lint"],
            typecheck=["npm", "run", "typecheck"],
        )
    if (project_root / "Cargo.toml").is_file():
        return ProjectConfig(
            name=project_name,
            language="Rust",
            build=["cargo", "build"],
            test=["cargo", "test"],
            lint=["cargo", "clippy", "--", "-D", "warnings"],
            typecheck=["cargo", "check"],
        )
    if (project_root / "go.mod").is_file():
        return ProjectConfig(
            name=project_name,
            language="Go",
            build=["go", "build", "./..."],
            test=["go", "test", "./..."],
            lint=["go", "vet", "./..."],
            typecheck=None,
        )
    return ProjectConfig(
        name=project_name,
        language="Unknown",
        build=["false"],
        test=["false"],
        lint=["false"],
        typecheck=None,
    )


def default_sensors(project: ProjectConfig) -> list[CommandSpec]:
    commands: list[tuple[str, list[str], int]] = [
        ("build", project.build, 900),
        ("lint", project.lint, 600),
        ("test", project.test, 1200),
    ]
    if project.typecheck:
        commands.insert(2, ("typecheck", project.typecheck, 900))
    return [
        CommandSpec(name=name, argv=argv, timeout_seconds=timeout)
        for name, argv, timeout in commands
    ]


def default_policy(project: ProjectConfig) -> PolicyConfig:
    project_commands = [project.build, project.test, project.lint]
    if project.typecheck:
        project_commands.append(project.typecheck)
    allow = _deduplicate_commands(
        [
            ["git", "status"],
            ["git", "diff"],
            ["git", "log"],
            ["git", "show"],
            ["rg"],
            *project_commands,
        ]
    )
    return PolicyConfig(
        allow_execute=allow,
        ask_execute=[
            ["git", "commit"],
            ["git", "push"],
            ["git", "tag"],
            ["gh"],
            ["npm", "publish"],
            ["uv", "publish"],
            ["curl"],
            ["wget"],
        ],
        deny_execute=[
            ["rm", "-rf"],
            ["sudo"],
            ["git", "clean", "-fd"],
            ["git", "reset", "--hard"],
            ["kubectl", "delete"],
            ["terraform", "destroy"],
        ],
        write_paths=[
            "src/**",
            "tests/**",
            "docs/**",
            "skills/**",
            ".hexaharness/**",
            "AGENTS.md",
            "CLAUDE.md",
        ],
        ask_write_paths=["**/*"],
    )


def default_trip_wires() -> list[TripWireSpec]:
    return [
        TripWireSpec(
            name="repeated-error",
            metric="same_error_count",
            threshold=3,
            response="pause-and-escalate",
        ),
        TripWireSpec(
            name="tool-budget",
            metric="tool_calls",
            threshold=50,
            response="stop-and-preserve-state",
        ),
        TripWireSpec(
            name="cost-budget",
            metric="cost_usd",
            threshold=5,
            response="pause-and-inspect",
        ),
        TripWireSpec(
            name="sensor-regression",
            metric="required_sensor_failure",
            threshold=1,
            response="block-completion",
        ),
    ]


def default_config(project_root: Path, name: str | None = None) -> HarnessConfig:
    project = infer_project(project_root, name)
    return HarnessConfig(
        project=project,
        sensors=default_sensors(project),
        budgets=BudgetConfig(),
        policy=default_policy(project),
        trust=TrustConfig(),
        trip_wires=default_trip_wires(),
        retention=RetentionConfig(),
    )


def load_config(project_root: Path) -> HarnessConfig:
    path = HarnessPaths(project_root).config
    if not path.is_file():
        raise FileNotFoundError(f"HexaHarness is not initialized at {project_root}")
    with path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    return HarnessConfig.model_validate(raw)


def save_config(project_root: Path, config: HarnessConfig) -> None:
    serialized = yaml.safe_dump(
        config.model_dump(mode="json"), sort_keys=False, allow_unicode=False, width=100
    )
    atomic_write_text(HarnessPaths(project_root).config, serialized)
