from __future__ import annotations

from pathlib import Path

from hexaharness.config import load_config
from hexaharness.models import PolicyDecision
from hexaharness.policy import evaluate_command, evaluate_path


def test_policy_uses_deny_ask_allow_precedence(harness_project: Path) -> None:
    config = load_config(harness_project)
    config.policy.allow_execute.append(["rm"])
    config.policy.ask_execute.append(["rm", "-rf"])

    denied = evaluate_command(config, ["rm", "-rf", "build"])
    ask = evaluate_command(config, ["git", "push", "origin", "main"])
    allowed = evaluate_command(config, [config.project.test[0], "-V"])
    unknown = evaluate_command(config, ["unlisted-command"])

    assert denied.decision == PolicyDecision.DENY
    assert ask.decision == PolicyDecision.ASK
    assert allowed.decision == PolicyDecision.ALLOW
    assert unknown.decision == PolicyDecision.ASK


def test_path_policy_blocks_escape_and_secret_write(harness_project: Path) -> None:
    config = load_config(harness_project)
    outside = evaluate_path(
        config, harness_project, harness_project.parent / "outside.txt", write=True
    )
    secret = evaluate_path(config, harness_project, Path(".env"), write=True)
    source = evaluate_path(config, harness_project, Path("src/module.py"), write=True)
    unknown = evaluate_path(config, harness_project, Path("config/app.toml"), write=True)

    assert outside.decision == PolicyDecision.DENY
    assert secret.decision == PolicyDecision.DENY
    assert source.decision == PolicyDecision.ALLOW
    assert unknown.decision == PolicyDecision.ASK
