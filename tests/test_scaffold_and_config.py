from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from hexaharness.config import default_sensors, legacy_v1_policy, load_config, save_config
from hexaharness.events import record_event
from hexaharness.models import PolicyDecision
from hexaharness.paths import HarnessPaths, find_project_root
from hexaharness.policy import evaluate_command
from hexaharness.scaffold import initialize_project, render_legacy_v1_agents


def _write_python_project(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )


def test_initialize_preserves_existing_agent_guide(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("existing guide\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module example.test/sample\n", encoding="utf-8")

    config = initialize_project(tmp_path, project_name="sample")

    assert config.project.language == "Go"
    agent_guide = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert agent_guide.startswith("existing guide\n")
    assert "<!-- hexaharness:start -->" in agent_guide
    assert "Use the HexaHarness skill automatically" in agent_guide
    assert (tmp_path / "CLAUDE.md").is_file()
    assert HarnessPaths(tmp_path).config.is_file()
    assert any(HarnessPaths(tmp_path).events.glob("*.jsonl"))


def test_initialize_refuses_to_replace_config_without_force(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    initialize_project(tmp_path)
    with pytest.raises(FileExistsError):
        initialize_project(tmp_path)


def test_force_initialization_does_not_duplicate_managed_guides(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    (tmp_path / "AGENTS.md").write_text("custom\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("claude custom\n", encoding="utf-8")

    initialize_project(tmp_path)
    rules_path = HarnessPaths(tmp_path).guide_rules
    learned_rules = """schema_version: 1
rules:
  - rule_id: preserved
    created_at: 2026-09-01T00:00:00Z
    failure_class: missing-context
    failure_summary: A command was guessed.
    rule: Use the configured command.
    verification: A fresh run uses the configured command.
    active: true
"""
    rules_path.write_text(learned_rules, encoding="utf-8")
    initialize_project(tmp_path, lint=["ruff", "check", "src"], force=True)

    agent_guide = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert agent_guide.count("<!-- hexaharness:start -->") == 1
    assert "Lint: `ruff check src`" in agent_guide
    assert agent_guide.startswith("custom\n")
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8").count(
        "<!-- hexaharness:start -->"
    ) == 1
    assert rules_path.read_text(encoding="utf-8") == learned_rules
    assert "Source task: legacy-unlinked" in HarnessPaths(tmp_path).guides_markdown.read_text(
        encoding="utf-8"
    )


def test_find_project_root_from_nested_folder(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    initialize_project(tmp_path)
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == tmp_path


def test_config_rejects_unknown_fields(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    initialize_project(tmp_path)
    path = HarnessPaths(tmp_path).config
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["unexpected"] = True
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(tmp_path)


def test_load_config_validates_one_yaml_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_python_project(tmp_path)
    initialize_project(tmp_path)
    real_safe_load = yaml.safe_load
    reads = 0

    def counted_safe_load(stream: object) -> object:
        nonlocal reads
        reads += 1
        return real_safe_load(stream)

    monkeypatch.setattr("hexaharness.config.yaml.safe_load", counted_safe_load)

    assert load_config(tmp_path).schema_version == 2
    assert reads == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("language", "   "),
        ("build", ["   "]),
        ("test", ["pytest", "\t"]),
    ],
)
def test_config_rejects_blank_project_values(
    tmp_path: Path,
    field: str,
    value: str | list[str],
) -> None:
    _write_python_project(tmp_path)
    initialize_project(tmp_path)
    path = HarnessPaths(tmp_path).config
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["project"][field] = value
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValidationError, match=field):
        load_config(tmp_path)


def test_config_rejects_blank_sensor_arguments(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    initialize_project(tmp_path)
    path = HarnessPaths(tmp_path).config
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["sensors"][0]["argv"] = ["   "]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValidationError, match=r"sensors\.0\.argv"):
        load_config(tmp_path)


def test_empty_initialization_is_atomic_and_requires_a_project_profile(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="project stack was not detected"):
        initialize_project(tmp_path)

    assert not (tmp_path / ".hexaharness").exists()
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()


def test_explicit_project_profile_is_rendered_consistently(tmp_path: Path) -> None:
    config = initialize_project(
        tmp_path,
        project_name="custom",
        project_language="Python",
        build=["python", "-m", "build"],
        test=["python", "-m", "pytest"],
        lint=["ruff", "check", "."],
    )

    guide = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert config.project.language == "Python"
    assert "Build: `python -m build`" in guide
    assert "Test: `python -m pytest`" in guide
    assert "Unknown" not in guide
    assert "`false`" not in guide


def test_malformed_managed_guide_blocks_refresh(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    (tmp_path / "AGENTS.md").write_text(
        "custom\n<!-- hexaharness:start -->\nbroken\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="malformed HexaHarness managed block"):
        initialize_project(tmp_path)
    assert not (tmp_path / ".hexaharness").exists()


def test_reversed_managed_markers_fail_before_initialization(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    (tmp_path / "AGENTS.md").write_text(
        "<!-- hexaharness:end -->\ncontent\n<!-- hexaharness:start -->\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="malformed HexaHarness managed block"):
        initialize_project(tmp_path)
    assert not (tmp_path / ".hexaharness").exists()


def test_force_refuses_ambiguous_legacy_content_outside_managed_block(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    agents_path = tmp_path / "AGENTS.md"
    original = (
        "# old agent guide\n\n"
        "PROJECT: old\nLANGUAGE: Unknown\nBUILD: `false`\nTEST: `false`\nLINT: `false`\n\n"
        "<!-- hexaharness:start -->\nold block\n<!-- hexaharness:end -->\n\n"
        "## Harness contract\n\nPreserve this contract.\n"
    )
    agents_path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match=r"customized legacy AGENTS\.md"):
        initialize_project(tmp_path, project_name="sample", force=True)

    assert agents_path.read_text(encoding="utf-8") == original
    assert not (tmp_path / ".hexaharness").exists()


def test_force_migrates_real_v1_guide_and_policy(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    config = initialize_project(tmp_path, project_name="Legacy")
    config.schema_version = 1
    config.policy = legacy_v1_policy(config.project)
    save_config(tmp_path, config)
    (tmp_path / "AGENTS.md").write_text(render_legacy_v1_agents(config), encoding="utf-8")

    migrated = initialize_project(tmp_path, force=True)

    guide = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert migrated.schema_version == 2
    assert migrated.project.name == "Legacy"
    assert evaluate_command(migrated, ["git", "commit", "-m", "local"]).decision == (
        PolicyDecision.ALLOW
    )
    assert "Start material work with `hexa start`" not in guide
    assert guide.count("<!-- hexaharness:start -->") == 1


def test_force_refuses_to_overwrite_customized_v1_guide(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    config = initialize_project(tmp_path, project_name="Legacy")
    config.schema_version = 1
    config.policy = legacy_v1_policy(config.project)
    save_config(tmp_path, config)
    agents_path = tmp_path / "AGENTS.md"
    customized = render_legacy_v1_agents(config) + "\n## Team rules\n\n- Preserve this section.\n"
    agents_path.write_text(customized, encoding="utf-8")
    original_config = HarnessPaths(tmp_path).config.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match=r"customized legacy AGENTS\.md"):
        initialize_project(tmp_path, force=True)

    assert agents_path.read_text(encoding="utf-8") == customized
    assert HarnessPaths(tmp_path).config.read_text(encoding="utf-8") == original_config


def test_v1_migration_does_not_restore_removed_command_allow(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    config = initialize_project(tmp_path, project_name="Restricted")
    config.schema_version = 1
    config.policy = legacy_v1_policy(config.project)
    removed = config.project.test
    config.policy.allow_execute = [
        command for command in config.policy.allow_execute if command != removed
    ]
    config.policy.unknown_execute = PolicyDecision.DENY
    save_config(tmp_path, config)

    migrated = initialize_project(tmp_path, force=True)

    assert removed not in migrated.policy.allow_execute
    assert migrated.policy.unknown_execute == PolicyDecision.DENY
    assert evaluate_command(migrated, removed).decision == PolicyDecision.DENY


def test_force_preserves_custom_configuration(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    config = initialize_project(tmp_path, project_name="Custom")
    config.budgets.max_tool_calls = 999
    config.policy.deny_execute.append(["custom-danger"])
    save_config(tmp_path, config)

    refreshed = initialize_project(tmp_path, force=True)

    assert refreshed.project.name == "Custom"
    assert refreshed.budgets.max_tool_calls == 999
    assert ["custom-danger"] in refreshed.policy.deny_execute


def test_force_refuses_to_downgrade_future_schema(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    config = initialize_project(tmp_path)
    config.schema_version = 999
    save_config(tmp_path, config)
    config_path = HarnessPaths(tmp_path).config
    original = config_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="requires a newer HexaHarness runtime"):
        initialize_project(tmp_path, force=True)

    assert config_path.read_text(encoding="utf-8") == original


def test_missing_schema_discriminator_is_treated_as_legacy(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    config = initialize_project(tmp_path)
    config.schema_version = 1
    config.policy = legacy_v1_policy(config.project)
    save_config(tmp_path, config)
    path = HarnessPaths(tmp_path).config
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    del raw["schema_version"]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    loaded = load_config(tmp_path)
    (tmp_path / "AGENTS.md").write_text(render_legacy_v1_agents(loaded), encoding="utf-8")

    migrated = initialize_project(tmp_path, force=True)

    assert loaded.schema_version == 1
    assert migrated.schema_version == 2
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8").count(
        "<!-- hexaharness:start -->"
    ) == 1


def test_force_redetects_path_qualified_placeholder_commands(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    config = initialize_project(tmp_path)
    config.schema_version = 1
    config.project.build = ["/usr/bin/false"]
    config.project.test = ["false", "--ignored"]
    config.project.lint = ["/usr/bin/true"]
    config.sensors = default_sensors(config.project)
    config.policy = legacy_v1_policy(config.project)
    save_config(tmp_path, config)

    migrated = initialize_project(tmp_path, force=True)

    assert migrated.project.build == ["uv", "build"]
    assert migrated.project.test == ["uv", "run", "pytest"]
    assert migrated.project.lint == ["uv", "run", "ruff", "check", "."]
    assert all(sensor.argv[0] not in {"false", "true"} for sensor in migrated.sensors)


def test_custom_v1_policy_refreshes_allowed_placeholder_capabilities(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    config = initialize_project(tmp_path)
    config.schema_version = 1
    config.project.build = ["/usr/bin/false"]
    config.project.test = ["/usr/bin/false"]
    config.project.lint = ["/usr/bin/false"]
    config.project.typecheck = None
    config.sensors = default_sensors(config.project)
    config.policy = legacy_v1_policy(config.project)
    config.policy.unknown_execute = PolicyDecision.DENY
    save_config(tmp_path, config)

    migrated = initialize_project(tmp_path, force=True)

    assert migrated.policy.unknown_execute == PolicyDecision.DENY
    assert migrated.project.build in migrated.policy.allow_execute
    assert migrated.project.test in migrated.policy.allow_execute
    assert migrated.project.lint in migrated.policy.allow_execute
    assert migrated.project.typecheck not in migrated.policy.allow_execute
    assert "typecheck" not in {sensor.name for sensor in migrated.sensors}
    assert ["/usr/bin/false"] not in migrated.policy.allow_execute
    assert migrated.policy.allow_execute[-3:] == [
        migrated.project.build,
        migrated.project.test,
        migrated.project.lint,
    ]


def test_force_adds_runtime_ignore_to_v1_block(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    (tmp_path / ".gitignore").write_text(
        "# HexaHarness runtime evidence\n.hexaharness/artifacts/\n.hexaharness/state/\n",
        encoding="utf-8",
    )

    initialize_project(tmp_path)

    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".hexaharness/runtime/" in gitignore
    assert ".hexaharness/external-action.key" in gitignore
    assert gitignore.count("# HexaHarness runtime evidence") == 1


def test_harness_symlink_is_rejected(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    victim = tmp_path.parent / f"{tmp_path.name}-victim"
    victim.mkdir()
    (tmp_path / ".hexaharness").symlink_to(victim, target_is_directory=True)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        initialize_project(tmp_path)
    assert not any(victim.iterdir())


def test_config_symlink_is_rejected_before_read_or_force_write(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    harness_dir = tmp_path / ".hexaharness"
    harness_dir.mkdir()
    victim = tmp_path.parent / f"{tmp_path.name}-config-victim.yaml"
    victim.write_text("schema_version: 999\n", encoding="utf-8")
    (harness_dir / "harness.yaml").symlink_to(victim)

    with pytest.raises(ValueError, match="configuration cannot be a symlink"):
        load_config(tmp_path)
    with pytest.raises(ValueError, match="configuration cannot be a symlink"):
        initialize_project(tmp_path, force=True)

    assert victim.read_text(encoding="utf-8") == "schema_version: 999\n"


def test_non_file_agent_guide_fails_before_writes(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    (tmp_path / "AGENTS.md").mkdir()

    with pytest.raises(ValueError, match="must be a regular file"):
        initialize_project(tmp_path)
    assert not (tmp_path / ".hexaharness").exists()


def test_dangling_agent_guide_symlink_fails_before_writes(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    missing_target = tmp_path.parent / f"{tmp_path.name}-missing-agent-guide"
    (tmp_path / "AGENTS.md").symlink_to(missing_target)

    with pytest.raises(ValueError, match="must be a regular file"):
        initialize_project(tmp_path)

    assert (tmp_path / "AGENTS.md").is_symlink()
    assert not missing_target.exists()
    assert not (tmp_path / ".hexaharness").exists()


def test_managed_child_symlink_is_rejected_before_initialization_writes(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    harness_dir = tmp_path / ".hexaharness"
    harness_dir.mkdir()
    victim = tmp_path.parent / f"{tmp_path.name}-events-victim"
    victim.mkdir()
    (harness_dir / "events").symlink_to(victim, target_is_directory=True)

    with pytest.raises(ValueError, match="event directory cannot be a symlink"):
        initialize_project(tmp_path)

    assert not (harness_dir / "harness.yaml").exists()
    assert not any(victim.iterdir())


def test_managed_child_symlink_blocks_later_runtime_writes(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    initialize_project(tmp_path)
    events = HarnessPaths(tmp_path).events
    for event_file in events.iterdir():
        event_file.unlink()
    events.rmdir()
    victim = tmp_path.parent / f"{tmp_path.name}-runtime-victim"
    victim.mkdir()
    events.symlink_to(victim, target_is_directory=True)

    with pytest.raises(ValueError, match="event directory cannot be a symlink"):
        record_event(tmp_path, "should-not-escape")

    assert not any(victim.iterdir())


def test_dynamic_managed_paths_reject_parent_traversal(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    initialize_project(tmp_path)
    paths = HarnessPaths(tmp_path)

    with pytest.raises(ValueError, match="single safe path component"):
        paths.state_file("../escaped")
    with pytest.raises(ValueError, match="single safe path component"):
        paths.task_artifacts("../escaped")
