from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from hexaharness import __version__
from hexaharness.cli import app

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

    completed = runner.invoke(app, ["complete", task_id, "--root", str(harness_project)])
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
    assert json.loads(result.stdout)["summary"] == {"fail": 0, "pass": 10, "warn": 2}


def test_cli_initializes_with_explicit_commands(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            "--root",
            str(tmp_path),
            "--project-name",
            "fresh",
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


def test_cli_run_status_stop_and_resume(harness_project: Path) -> None:
    started = runner.invoke(app, ["start", "Lifecycle", "--root", str(harness_project)])
    task_id = json.loads(started.stdout)["task_id"]
    executed = runner.invoke(
        app,
        [
            "run",
            task_id,
            "--root",
            str(harness_project),
            "--",
            sys.executable,
            "-c",
            "print('through cli')",
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
    learned = runner.invoke(
        app,
        [
            "learn",
            "--root",
            str(harness_project),
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
