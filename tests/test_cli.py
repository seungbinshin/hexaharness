from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from hexaharness import __version__
from hexaharness.cli import app
from hexaharness.config import load_config, save_config
from hexaharness.events import iter_events

runner = CliRunner()


def test_cli_version_flag() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_cli_full_happy_path(harness_project: Path) -> None:
    doctor = runner.invoke(app, ["doctor", "--root", str(harness_project)])
    assert doctor.exit_code == 0
    assert json.loads(doctor.stdout)["status"] == "ok"

    started = runner.invoke(app, ["start", "CLI task", "--root", str(harness_project)])
    assert started.exit_code == 0
    task_id = json.loads(started.stdout)["task_id"]

    checkpointed = runner.invoke(
        app,
        [
            "checkpoint",
            task_id,
            "--root",
            str(harness_project),
            "--completed",
            "one step",
            "--next",
            "verify",
        ],
    )
    assert checkpointed.exit_code == 0

    verified = runner.invoke(app, ["verify", task_id, "--root", str(harness_project)])
    assert verified.exit_code == 0

    cleared = runner.invoke(
        app,
        [
            "checkpoint",
            task_id,
            "--root",
            str(harness_project),
            "--completed",
            "verified sensors",
            "--clear-next",
        ],
    )
    assert cleared.exit_code == 0
    assert json.loads(cleared.stdout)["next_step"] is None

    observed = runner.invoke(
        app,
        [
            "policy-check",
            "--root",
            str(harness_project),
            "--task-id",
            task_id,
            "--path",
            "src/cli-result.txt",
            "--write",
        ],
    )
    assert observed.exit_code == 0
    assert json.loads(observed.stdout)["write_observation_id"].startswith("write-")
    (harness_project / "src").mkdir()
    (harness_project / "src" / "cli-result.txt").write_text("result\n", encoding="utf-8")
    completed = runner.invoke(
        app,
        [
            "complete",
            task_id,
            "--root",
            str(harness_project),
            "--artifact",
            "src/cli-result.txt",
        ],
    )
    assert completed.exit_code == 0
    assert json.loads(completed.stdout)["status"] == "completed"


def test_cli_policy_check_exit_codes(harness_project: Path) -> None:
    ask = runner.invoke(
        app,
        [
            "policy-check",
            "--root",
            str(harness_project),
            "--",
            "git",
            "push",
        ],
    )
    deny = runner.invoke(
        app,
        [
            "policy-check",
            "--root",
            str(harness_project),
            "--",
            "rm",
            "-rf",
            "build",
        ],
    )
    assert ask.exit_code == 3
    assert json.loads(ask.stdout)["decision"] == "ask"
    assert deny.exit_code == 2
    assert json.loads(deny.stdout)["decision"] == "deny"


def test_cli_audit_json(harness_project: Path) -> None:
    result = runner.invoke(app, ["audit", "--root", str(harness_project), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["summary"] == {"fail": 0, "pass": 3, "warn": 9}


def test_cli_initializes_with_explicit_commands(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            "--root",
            str(tmp_path),
            "--project-name",
            "fresh",
            "--language",
            "Python",
            "--build",
            "python -V",
            "--test",
            "python -V",
            "--lint",
            "python -V",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["project"]["name"] == "fresh"
    assert payload["project"]["build"] == ["python", "-V"]
    assert (tmp_path / ".hexaharness" / "harness.yaml").is_file()


def test_cli_rejects_empty_project_before_writing_files(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--root", str(tmp_path)])

    assert result.exit_code != 0
    assert "project stack was not detected" in str(result.exception)
    assert not (tmp_path / ".hexaharness").exists()


def test_cli_rejects_blank_language_override_before_writing_files(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )

    result = runner.invoke(
        app,
        ["init", "--root", str(tmp_path), "--language", "   "],
    )

    assert result.exit_code != 0
    assert "non-whitespace" in str(result.exception)
    assert not (tmp_path / ".hexaharness").exists()


def test_doctor_and_audit_reject_legacy_placeholder_configuration(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    initialized = runner.invoke(app, ["init", "--root", str(tmp_path)])
    assert initialized.exit_code == 0
    config_path = tmp_path / ".hexaharness" / "harness.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["project"].update(
        {"language": "Unknown", "build": ["false"], "test": ["false"], "lint": ["false"]}
    )
    for sensor in raw["sensors"]:
        sensor["argv"] = ["false"]
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    audit = runner.invoke(app, ["audit", "--root", str(tmp_path), "--json"])
    verified = runner.invoke(app, ["verify", "--root", str(tmp_path)])

    assert doctor.exit_code == 2
    assert json.loads(doctor.stdout)["status"] == "needs-configuration"
    assert audit.exit_code == 1
    summary = json.loads(audit.stdout)["summary"]
    assert summary["fail"] >= 2
    assert verified.exit_code != 0
    assert not any((tmp_path / ".hexaharness" / "artifacts").rglob("sensor-*.log"))


def test_doctor_tells_future_schema_users_to_upgrade(harness_project: Path) -> None:
    config_path = harness_project / ".hexaharness" / "harness.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["schema_version"] = 999
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--root", str(harness_project)])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "needs-configuration"
    assert payload["schema_version"] == 999
    assert payload["sensors"] == []
    assert payload["budgets"] is None
    assert "upgrade the HexaHarness runtime" in payload["next_action"]
    with pytest.raises(ValueError, match="requires a newer HexaHarness runtime"):
        load_config(harness_project)

    status = runner.invoke(app, ["status", "--root", str(harness_project)])
    assert status.exit_code != 0
    assert "requires a newer HexaHarness runtime" in str(status.exception)


@pytest.mark.parametrize(
    "placeholder",
    [
        ["/usr/bin/false"],
        ["/bin/true", "--ignored"],
        ["FALSE.exe"],
    ],
)
def test_doctor_rejects_path_qualified_required_sensor_placeholders(
    harness_project: Path,
    placeholder: list[str],
) -> None:
    config = load_config(harness_project)
    config.sensors[0].argv = placeholder
    save_config(harness_project, config)

    result = runner.invoke(app, ["doctor", "--root", str(harness_project)])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "needs-configuration"
    assert any("sensor 'test' uses a no-op placeholder" in issue for issue in payload["issues"])


def test_cli_run_status_stop_and_resume(harness_project: Path) -> None:
    started = runner.invoke(app, ["start", "Lifecycle", "--root", str(harness_project)])
    task_id = json.loads(started.stdout)["task_id"]
    command = [sys.executable, "-c", "print('through cli')"]
    config = load_config(harness_project)
    config.policy.allow_execute.append(command)
    save_config(harness_project, config)
    executed = runner.invoke(
        app,
        [
            "run",
            task_id,
            "--root",
            str(harness_project),
            "--",
            *command,
        ],
    )
    assert executed.exit_code == 0
    assert json.loads(executed.stdout)["exit_code"] == 0

    status = runner.invoke(app, ["status", task_id, "--root", str(harness_project)])
    assert json.loads(status.stdout)["tool_calls"] == 1

    stopped = runner.invoke(app, ["stop", "--root", str(harness_project), "--reason", "incident"])
    assert stopped.exit_code == 0
    assert json.loads(stopped.stdout)["status"] == "stopped"

    resumed = runner.invoke(
        app,
        ["resume", task_id, "--root", str(harness_project), "--clear-stop"],
    )
    assert resumed.exit_code == 0


def test_cli_records_learning_and_strict_audit(harness_project: Path) -> None:
    started = runner.invoke(app, ["start", "Observed failure", "--root", str(harness_project)])
    task_id = json.loads(started.stdout)["task_id"]
    failing_script = harness_project / "failure-command.py"
    failing_script.write_text("raise SystemExit(7)\n", encoding="utf-8")
    command = [sys.executable, failing_script.name]
    config = load_config(harness_project)
    config.policy.allow_execute.append(command)
    save_config(harness_project, config)
    failed = runner.invoke(
        app,
        [
            "run",
            task_id,
            "--root",
            str(harness_project),
            "--",
            *command,
        ],
    )
    assert failed.exit_code == 1
    failed_payload = json.loads(failed.stdout)
    assert failed_payload["exit_code"] == 7
    failure_event = next(
        event
        for event in reversed(list(iter_events(harness_project)))
        if event.event_type == "command.failed" and event.task_id == task_id
    )
    evidence_path = str(failure_event.payload["output_path"])
    learned = runner.invoke(
        app,
        [
            "learn",
            "--root",
            str(harness_project),
            "--task-id",
            task_id,
            "--evidence",
            evidence_path,
            "--class",
            "missing-context",
            "--summary",
            "The agent guessed a command",
            "--fix",
            "Record the command",
            "--verification",
            "A fresh run uses it",
            "--layer",
            "guide",
            "--guide-rule",
            "Use the configured command.",
        ],
    )
    assert learned.exit_code == 0
    assert json.loads(learned.stdout)["target_layer"] == "guide"

    strict = runner.invoke(app, ["audit", "--root", str(harness_project), "--strict"])
    assert strict.exit_code == 1
    assert "WARN" in strict.stdout


def test_cli_policy_check_for_path_and_empty_run(harness_project: Path) -> None:
    path_check = runner.invoke(
        app,
        [
            "policy-check",
            "--root",
            str(harness_project),
            "--path",
            "src/module.py",
            "--write",
        ],
    )
    assert path_check.exit_code == 0
    assert json.loads(path_check.stdout)["decision"] == "allow"

    started = runner.invoke(app, ["start", "No command", "--root", str(harness_project)])
    task_id = json.loads(started.stdout)["task_id"]
    empty = runner.invoke(app, ["run", task_id, "--root", str(harness_project)])
    assert empty.exit_code != 0


def test_policy_check_records_redacted_task_evidence(harness_project: Path) -> None:
    started = runner.invoke(app, ["start", "Policy evidence", "--root", str(harness_project)])
    task_id = json.loads(started.stdout)["task_id"]

    checked = runner.invoke(
        app,
        [
            "policy-check",
            "--root",
            str(harness_project),
            "--task-id",
            task_id,
            "--",
            "local-tool",
            "--token",
            "hunter2",
        ],
    )

    assert checked.exit_code == 3
    assert "hunter2" not in checked.stdout
    event = next(
        item
        for item in reversed(list(iter_events(harness_project)))
        if item.event_type == "policy.command-check"
    )
    assert event.task_id == task_id
    assert "hunter2" not in json.dumps(event.payload)

    assert event.payload["argv"][-1] == "<redacted>"


def test_prepare_external_stages_a_digest_bound_redacted_action(
    harness_project: Path,
) -> None:
    started = runner.invoke(app, ["start", "Publish", "--root", str(harness_project)])
    task_id = json.loads(started.stdout)["task_id"]

    prepared = runner.invoke(
        app,
        [
            "prepare-external",
            task_id,
            "--root",
            str(harness_project),
            "--",
            "git",
            "push",
            "origin",
            "main",
            "--token",
            "hunter2",
        ],
    )

    assert prepared.exit_code == 0
    assert "hunter2" not in prepared.stdout
    payload = json.loads(prepared.stdout)
    assert payload["approval_required"] is True
    assert payload["next_step"].startswith("PENDING_EXTERNAL_ACTION: git push origin main")
    assert "[action-nonce:" in payload["next_step"]
    assert ";argv-hmac-sha256:" in payload["next_step"]
    state = runner.invoke(app, ["status", task_id, "--root", str(harness_project)])
    assert json.loads(state.stdout)["next_step"] == payload["next_step"]
    event = next(
        item
        for item in reversed(list(iter_events(harness_project)))
        if item.event_type == "external-action.pending"
    )
    assert event.task_id == task_id
    assert "hunter2" not in json.dumps(event.payload)

    cancelled = runner.invoke(
        app,
        [
            "checkpoint",
            task_id,
            "--root",
            str(harness_project),
            "--completed",
            "publication declined",
            "--cancel-external-action",
        ],
    )
    assert cancelled.exit_code == 0
    assert json.loads(cancelled.stdout)["next_step"] is None
