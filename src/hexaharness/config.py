from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

from hexaharness.errors import PolicyBlockedError
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
from hexaharness.policy import evaluate_command

CURRENT_SCHEMA_VERSION = 2


def _deduplicate_commands(commands: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    result: list[list[str]] = []
    for command in commands:
        key = tuple(command)
        if key not in seen:
            seen.add(key)
            result.append(command)
    return result


def infer_project(project_root: Path, name: str | None = None) -> ProjectConfig | None:
    project_name = name or project_root.name
    if (project_root / "pyproject.toml").is_file():
        with (project_root / "pyproject.toml").open("rb") as stream:
            document = tomllib.load(stream)
        # Optional sensors must have project evidence; not every Python project
        # uses mypy or a src/ layout.
        uses_mypy = "mypy" in document.get("tool", {}) or (project_root / "mypy.ini").is_file()
        return ProjectConfig(
            name=project_name,
            language="Python",
            build=["uv", "build"],
            test=["uv", "run", "pytest"],
            lint=["uv", "run", "ruff", "check", "."],
            typecheck=(
                ["uv", "run", "mypy", "src" if (project_root / "src").is_dir() else "."]
                if uses_mypy
                else None
            ),
        )
    if (project_root / "package.json").is_file():
        document = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
        manager = str(document.get("packageManager", "")).split("@", 1)[0]
        if manager not in {"npm", "pnpm", "yarn"}:
            manager = (
                "pnpm"
                if (project_root / "pnpm-lock.yaml").is_file()
                else ("yarn" if (project_root / "yarn.lock").is_file() else "npm")
            )
        return ProjectConfig(
            name=project_name,
            language="TypeScript/JavaScript",
            build=[manager, "run", "build"],
            test=[manager, "run", "test"],
            lint=[manager, "run", "lint"],
            typecheck=[manager, "run", "typecheck"]
            if "typecheck" in document.get("scripts", {})
            else None,
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
    return None


def resolve_project(
    project_root: Path,
    name: str | None = None,
    *,
    language: str | None = None,
    build: list[str] | None = None,
    test: list[str] | None = None,
    lint: list[str] | None = None,
    typecheck: list[str] | None = None,
) -> ProjectConfig:
    """Resolve a complete project profile before initialization mutates the repository."""
    detected = infer_project(project_root, name)
    if detected is not None and detected.language == "TypeScript/JavaScript":
        document = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
        scripts = document.get("scripts", {})
        missing = [
            field
            for field, override in {"build": build, "test": test, "lint": lint}.items()
            if override is None and not scripts.get(field)
        ]
        if missing:
            raise ValueError(
                "package.json has no script for "
                + ", ".join(missing)
                + "; provide exact "
                + ", ".join(f"--{field}" for field in missing)
                + " commands"
            )
    if detected is None:
        missing = [
            field
            for field, value in {
                "language": language,
                "build": build,
                "test": test,
                "lint": lint,
            }.items()
            if value is None
        ]
        if missing:
            fields = ", ".join(f"--{field}" for field in missing)
            raise ValueError(
                "project stack was not detected; scaffold a supported manifest first or provide "
                f"exact {fields} values"
            )
        if language is None or not language.strip():
            raise ValueError("project language must contain a non-whitespace value")
        return ProjectConfig(
            name=name or project_root.name,
            language=language.strip(),
            build=build or [],
            test=test or [],
            lint=lint or [],
            typecheck=typecheck,
        )

    values = detected.model_dump()
    if language is not None:
        if not language.strip():
            raise ValueError("project language must contain a non-whitespace value")
        values["language"] = language.strip()
    for field, command in {
        "build": build,
        "test": test,
        "lint": lint,
        "typecheck": typecheck,
    }.items():
        if command is not None:
            values[field] = command
    return ProjectConfig.model_validate(values)


def is_placeholder_command(command: list[str]) -> bool:
    """Return whether a command is an explicit no-op placeholder rather than a real sensor."""
    return bool(command) and Path(command[0]).name.casefold().removesuffix(".exe") in {
        "false",
        "true",
    }


def configuration_issues(config: HarnessConfig, project_root: Path | None = None) -> list[str]:
    """Return semantic readiness problems that schema validation alone cannot detect."""
    issues: list[str] = []
    if config.schema_version < CURRENT_SCHEMA_VERSION:
        issues.append(
            f"schema_version {config.schema_version} requires migration to {CURRENT_SCHEMA_VERSION}"
        )
    elif config.schema_version > CURRENT_SCHEMA_VERSION:
        issues.append(
            f"schema_version {config.schema_version} is newer than supported "
            f"{CURRENT_SCHEMA_VERSION}"
        )
    if not config.project.language.strip():
        issues.append("project language is blank")
    elif config.project.language.strip().casefold() == "unknown":
        issues.append("project language is Unknown")
    for name in ("build", "test", "lint", "typecheck"):
        command = getattr(config.project, name)
        if command is not None and is_placeholder_command(command):
            issues.append(f"project {name} command uses a no-op placeholder")
    usable_required = [
        sensor
        for sensor in config.sensors
        if sensor.required and not is_placeholder_command(sensor.argv)
    ]
    if not usable_required:
        issues.append("no usable required computational sensor is configured")
    for sensor in config.sensors:
        if is_placeholder_command(sensor.argv):
            issues.append(f"sensor {sensor.name!r} uses a no-op placeholder")
        elif project_root is not None and sensor.required:
            outcome = evaluate_command(config, sensor.argv, project_root=project_root)
            if outcome.decision.value != "allow":
                issues.append(
                    f"required sensor {sensor.name!r} is not allow-listed: {outcome.reason}"
                )
    return issues


def ensure_configuration_ready(config: HarnessConfig, project_root: Path | None = None) -> None:
    """Reject provisional configurations before task execution or verification."""
    if issues := configuration_issues(config, project_root):
        raise ValueError("HexaHarness configuration needs repair: " + "; ".join(issues))


def ensure_configuration_current(project_root: Path, config: HarnessConfig) -> None:
    """Reject a caller snapshot that no longer matches the durable configuration."""
    if load_config(project_root) != config:
        raise PolicyBlockedError(
            "supplied HexaHarness configuration is stale; reload harness.yaml before executing work"
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
            ["git", "add"],
            ["git", "commit"],
            ["git", "tag"],
            ["git", "branch"],
            ["rg"],
            ["gh", "--version"],
            ["gh", "auth", "status"],
            ["gh", "help"],
            ["gh", "status"],
            ["gh", "issue", "list"],
            ["gh", "issue", "status"],
            ["gh", "issue", "view"],
            ["gh", "pr", "checks"],
            ["gh", "pr", "diff"],
            ["gh", "pr", "list"],
            ["gh", "pr", "status"],
            ["gh", "pr", "view"],
            ["gh", "release", "list"],
            ["gh", "release", "view"],
            ["gh", "repo", "list"],
            ["gh", "repo", "view"],
            ["gh", "run", "list"],
            ["gh", "run", "view"],
            ["gh", "workflow", "list"],
            ["gh", "workflow", "view"],
            ["uv", "sync"],
            ["uv", "lock"],
            ["uv", "add"],
            ["uv", "remove"],
            ["pytest"],
            ["ruff"],
            ["mypy"],
            ["npm", "install"],
            ["npm", "ci"],
            ["pnpm", "install"],
            ["pnpm", "add"],
            ["pnpm", "remove"],
            ["yarn", "install"],
            ["yarn", "add"],
            ["yarn", "remove"],
            ["cargo", "add"],
            ["cargo", "remove"],
            ["cargo", "update"],
            ["cargo", "fmt"],
            ["go", "get"],
            ["go", "mod", "download"],
            ["go", "mod", "tidy"],
            ["go", "fmt"],
            *project_commands,
        ]
    )
    return PolicyConfig(
        allow_execute=allow,
        ask_execute=[
            ["git", "commit", "--amend"],
            ["git", "branch", "-d"],
            ["git", "branch", "-D"],
            ["git", "branch", "--delete"],
            ["git", "tag", "-d"],
            ["git", "tag", "--delete"],
            ["git", "tag", "-f"],
            ["git", "push"],
            ["npm", "publish"],
            ["npm", "unpublish"],
            ["npm", "deprecate"],
            ["pnpm", "publish"],
            ["yarn", "npm", "publish"],
            ["uv", "publish"],
            ["cargo", "publish"],
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
        write_paths=["*", "**/*"],
        ask_write_paths=[],
        deny_write_paths=[
            ".env",
            ".env.*",
            "**/.env",
            "**/.env.*",
            "*credentials*",
            "**/credentials*",
            "*secret*",
            "**/*secret*",
            ".git/**",
        ],
    )


def legacy_v1_policy(project: ProjectConfig) -> PolicyConfig:
    """Reconstruct the 0.1 generated profile so migrations can preserve true custom rules."""
    project_commands = [project.build, project.test, project.lint]
    if project.typecheck:
        project_commands.append(project.typecheck)
    return PolicyConfig(
        allow_execute=_deduplicate_commands(
            [
                ["git", "status"],
                ["git", "diff"],
                ["git", "log"],
                ["git", "show"],
                ["rg"],
                *project_commands,
            ]
        ),
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


def migrate_v1_policy(
    existing: PolicyConfig, old_project: ProjectConfig, new: PolicyConfig
) -> PolicyConfig:
    """Upgrade an untouched v1 profile without widening a customized policy."""
    baseline = legacy_v1_policy(old_project)
    return new if existing == baseline else existing


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


def default_config(
    project_root: Path,
    name: str | None = None,
    *,
    language: str | None = None,
    build: list[str] | None = None,
    test: list[str] | None = None,
    lint: list[str] | None = None,
    typecheck: list[str] | None = None,
) -> HarnessConfig:
    project = resolve_project(
        project_root,
        name,
        language=language,
        build=build,
        test=test,
        lint=lint,
        typecheck=typecheck,
    )
    return HarnessConfig(
        project=project,
        sensors=default_sensors(project),
        budgets=BudgetConfig(),
        policy=default_policy(project),
        trust=TrustConfig(),
        trip_wires=default_trip_wires(),
        retention=RetentionConfig(),
    )


def _load_config_document(project_root: Path) -> dict[str, object]:
    """Read one complete configuration snapshot from disk."""
    path = HarnessPaths(project_root).config
    if not path.is_file():
        raise FileNotFoundError(f"HexaHarness is not initialized at {project_root}")
    with path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise ValueError("HexaHarness configuration must be a YAML mapping")
    return raw


def _config_schema_version(raw: dict[str, object]) -> int:
    version: object = raw.get("schema_version", 1)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("HexaHarness schema_version must be an integer")
    return version


def load_config_snapshot(project_root: Path) -> tuple[int, HarnessConfig | None]:
    """Read once, returning the version and a validated model when this runtime supports it."""
    raw = _load_config_document(project_root)
    schema_version = _config_schema_version(raw)
    if schema_version > CURRENT_SCHEMA_VERSION:
        return schema_version, None
    if "schema_version" not in raw:
        # Pre-versioned documents belong to the v1 format. Do not let the current model default
        # silently reinterpret them as current and bypass migration checks.
        raw = {"schema_version": 1, **raw}
    return schema_version, HarnessConfig.model_validate(raw)


def load_config(project_root: Path) -> HarnessConfig:
    schema_version, config = load_config_snapshot(project_root)
    if config is None:
        raise ValueError(
            f"schema_version {schema_version} requires a newer HexaHarness runtime; "
            f"this runtime supports {CURRENT_SCHEMA_VERSION}"
        )
    return config


def load_config_schema_version(project_root: Path) -> int:
    """Read only the schema discriminator so future configs can be rejected before mutation."""
    return _config_schema_version(_load_config_document(project_root))


def save_config(project_root: Path, config: HarnessConfig) -> None:
    serialized = yaml.safe_dump(
        config.model_dump(mode="json"), sort_keys=False, allow_unicode=False, width=100
    )
    # Keep replacement linearizable with commands, sensors, and final completion checks that rely
    # on the configured policy and evidence requirements.
    from hexaharness.state import configuration_lock

    with configuration_lock(project_root):
        atomic_write_text(HarnessPaths(project_root).config, serialized)
