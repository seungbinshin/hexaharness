from __future__ import annotations

from pathlib import Path

import pytest

from hexaharness.config import load_config
from hexaharness.models import PolicyConfig, PolicyDecision, ProjectConfig
from hexaharness.policy import evaluate_command, evaluate_path


def test_policy_uses_deny_ask_allow_precedence(harness_project: Path) -> None:
    config = load_config(harness_project)
    config.policy.allow_execute.append(["rm"])
    config.policy.ask_execute.append(["rm", "-rf"])

    denied = evaluate_command(config, ["rm", "-rf", "build"])
    ask = evaluate_command(config, ["git", "push", "origin", "main"])
    allowed = evaluate_command(config, config.project.test)
    unknown = evaluate_command(config, ["unlisted-command"])

    assert denied.decision == PolicyDecision.DENY
    assert ask.decision == PolicyDecision.ASK
    assert allowed.decision == PolicyDecision.ALLOW
    assert unknown.decision == PolicyDecision.ASK


@pytest.mark.parametrize(
    "argv",
    [
        ["rg", "--fixed-strings", "gh", "README.md"],
        ["git", "add", "terraform", "destroy"],
        ["git", "commit", "-m", "gh"],
        ["uv", "run", "pytest", "-k", "gh"],
        ["timeout", "10", "rg", "--fixed-strings", "gh", "README.md"],
    ],
)
def test_data_arguments_do_not_become_executable_commands(tmp_path: Path, argv: list[str]) -> None:
    from hexaharness.config import default_config

    (tmp_path / "pyproject.toml").write_text('[project]\nname="sample"\nversion="1"\n')
    config = default_config(tmp_path)
    assert evaluate_command(config, argv).decision == PolicyDecision.ALLOW


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

    for sensitive in (
        Path(".git"),
        Path("nested/.git/config"),
        Path(".ENV"),
        Path("Credentials.json"),
        Path("Secrets.toml"),
        Path(".npmrc"),
        Path(),
    ):
        assert evaluate_path(config, harness_project, sensitive, write=True).decision == (
            PolicyDecision.DENY
        )


def test_default_policy_allows_local_work_and_gates_publication(tmp_path: Path) -> None:
    from hexaharness.config import default_config

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    config = default_config(tmp_path)

    assert evaluate_command(config, ["git", "commit", "-m", "checkpoint"]).decision == (
        PolicyDecision.ALLOW
    )
    assert evaluate_command(config, ["uv", "sync", "--locked"]).decision == PolicyDecision.ALLOW
    assert evaluate_command(config, ["python", "script.py"]).decision == PolicyDecision.ASK
    assert not evaluate_command(config, ["python", "script.py"]).requires_human_approval
    assert evaluate_command(config, ["gh", "pr", "view", "123"]).decision == (PolicyDecision.ALLOW)
    assert evaluate_command(config, ["gh", "auth", "status", "--show-token"]).decision == (
        PolicyDecision.DENY
    )
    assert evaluate_command(config, ["gh", "auth", "status", "--show-token=true"]).decision == (
        PolicyDecision.DENY
    )
    assert evaluate_command(config, ["gh", "auth", "status", "-t=true"]).decision == (
        PolicyDecision.DENY
    )
    assert evaluate_command(config, ["gh", "auth", "token"]).decision == PolicyDecision.DENY
    assert evaluate_command(config, ["gh", "pr", "merge", "123"]).requires_human_approval
    assert evaluate_command(config, ["make", "deploy"]).requires_human_approval
    assert evaluate_command(
        config, ["/usr/bin/env", "gh", "issue", "comment", "1"]
    ).requires_human_approval
    assert evaluate_command(
        config, ["git", "send-pack", "origin", "HEAD:main"]
    ).requires_human_approval
    for argv in (
        ["kubectl", "apply", "-f", "deployment.yaml"],
        ["terraform", "apply", "saved.tfplan"],
        ["aws", "s3", "cp", "artifact.whl", "s3://release-bucket/artifact.whl"],
        ["aws", "route53", "change-resource-record-sets", "--hosted-zone-id", "Z1"],
        ["timeout", "--signal", "TERM", "30", "kubectl", "apply", "-f", "deployment.yaml"],
        ["nice", "--adjustment=5", "terraform", "apply", "saved.tfplan"],
        ["xargs", "kubectl", "apply", "-f", "deployment.yaml"],
        ["busybox", "sh", "-c", "git push origin main"],
        ["toybox", "sh", "-c", "git push origin main"],
        ["busybox", "git", "push", "origin", "main"],
        ["toybox", "kubectl", "apply", "-f", "deployment.yaml"],
        ["busybox", "--install", "/tmp/applets"],
        ["lua", "-e", "os.execute('git push origin main')"],
        ["lua5.4", "-eos.execute('git push origin main')"],
        ["julia", "--eval=run(`git push origin main`)"],
        ["deno", "eval", "new Deno.Command('git').output()"],
        ["stdbuf", "-oL", "git", "push", "origin", "main"],
        ["nohup", "git", "push", "origin", "main"],
        ["setsid", "-f", "git", "push", "origin", "main"],
        ["ionice", "-c2", "git", "push", "origin", "main"],
        ["taskset", "0x1", "git", "push", "origin", "main"],
        ["strace", "-o", "trace.log", "git", "push", "origin", "main"],
        ["uv", "run", "git", "push", "origin", "main"],
        ["npx", "--yes", "git", "push", "origin", "main"],
        ["python", "-m", "twine", "upload", "dist/pkg.whl"],
        ["pypy3", "-c", "run_external_action()"],
        ["python3", "-Ic", "run_external_action()"],
        ["pypy3", "-qIc", "run_external_action()"],
        ["nodejs", "-e", "runExternalAction()"],
        ["py", "-c", "run_external_action()"],
        ["npm", "exec", "--call", "gh pr merge 123"],
        ["npm", "x", "--cal=gh pr merge 123"],
        ["npx", "--call", "gh pr merge 123"],
        ["opaque-interpreter", "-e", "run_external_action()"],
        ["sh", "-xc", "git push origin main"],
        ["env", "--spl", "git send-pack origin HEAD:main"],
        ["deploy", "production"],
    ):
        outcome = evaluate_command(config, argv)
        assert outcome.decision == PolicyDecision.ASK
        assert outcome.requires_human_approval

    for argv in (
        ["kubectl", "get", "pods"],
        ["terraform", "plan"],
        ["aws", "s3", "ls", "s3://release-bucket"],
        ["timeout", "30", "kubectl", "get", "pods"],
        ["nice", "-n", "5", "terraform", "plan"],
    ):
        assert not evaluate_command(config, argv).requires_human_approval
    assert evaluate_command(config, ["npm", "pub"]).requires_human_approval
    assert evaluate_command(
        config, ["npm", "dist-tag", "add", "pkg@1", "latest"]
    ).requires_human_approval
    assert evaluate_command(config, ["cargo", "yank", "pkg"]).requires_human_approval
    assert evaluate_command(config, ["git", "tag", "release"]).decision == PolicyDecision.ALLOW
    assert evaluate_command(config, ["git", "tag", "-af", "release"]).requires_human_approval
    assert evaluate_command(config, ["git", "branch", "-M", "main"]).requires_human_approval
    assert evaluate_command(config, ["git", "branch", "-f", "main"]).requires_human_approval
    assert evaluate_command(config, ["git", "branch", "-C", "old", "main"]).requires_human_approval
    assert evaluate_command(config, ["git", "commit", "--amen"]).requires_human_approval
    assert evaluate_command(config, ["git", "branch", "--del", "old"]).requires_human_approval
    assert evaluate_command(config, ["git", "tag", "--forc", "v1"]).requires_human_approval
    assert evaluate_command(config, ["pytest", "release"]).decision == PolicyDecision.ALLOW
    assert evaluate_command(config, ["git", "-C", ".", "reset", "HEAD", "--hard"]).decision == (
        PolicyDecision.DENY
    )
    assert evaluate_command(config, ["rm", "-fr", "build"]).decision == PolicyDecision.DENY
    for argv in (
        ["busybox", "rm", "-rf", "src"],
        ["toybox", "rm", "--recursive", "--force", "src"],
        ["env", "busybox", "rm", "-fr", "src"],
        ["/bin/busybox", "nice", "-n", "5", "rm", "-rf", "src"],
        ["timeout", "30", "toybox", "rm", "-rf", "src"],
        ["stdbuf", "-oL", "rm", "-rf", "src"],
        ["nohup", "rm", "-rf", "src"],
        ["setsid", "-f", "rm", "-rf", "src"],
        ["ionice", "-c2", "rm", "-rf", "src"],
        ["taskset", "0x1", "rm", "-rf", "src"],
        ["strace", "-o", "trace.log", "rm", "-rf", "src"],
        ["uv", "run", "rm", "-rf", "src"],
        ["npx", "rm", "-rf", "src"],
        ["unknown-launcher", "rm", "-rf", "src"],
        ["git", "reset", "--har", "HEAD"],
        ["git", "reset", "--h", "HEAD"],
        ["rm", "--recurs", "--for", "src"],
        ["rm", "--r", "--f", "src"],
        ["git", "diff", "--ext", "HEAD"],
        ["git", "diff", "--textc", "HEAD"],
    ):
        assert evaluate_command(config, argv).decision == PolicyDecision.DENY
    assert evaluate_command(config, ["busybox", "git", "status"]).decision == (PolicyDecision.ALLOW)
    assert (
        evaluate_command(config, ["toybox", "gh", "auth", "status", "--show-token"]).decision
        == PolicyDecision.DENY
    )
    assert evaluate_command(config, ["rg", "--pre=/bin/sh", "needle", "deploy.sh"]).decision == (
        PolicyDecision.DENY
    )
    assert evaluate_command(
        config, ["kubectl", "--context", "dev", "delete", "pod", "x"]
    ).decision == (PolicyDecision.DENY)
    assert evaluate_command(config, ["npm", "unpublish", "package"]).decision == PolicyDecision.ASK
    assert evaluate_command(config, ["git", "branch", "-D", "old"]).decision == PolicyDecision.ASK
    assert evaluate_command(config, ["git", "commit", "-a", "--amend"]).decision == (
        PolicyDecision.ASK
    )
    assert evaluate_command(config, ["git", "tag", "release", "--force"]).decision == (
        PolicyDecision.ASK
    )
    assert evaluate_command(config, ["git", "push", "origin", "main"]).decision == (
        PolicyDecision.ASK
    )
    assert evaluate_command(config, ["npm", "publish"]).decision == PolicyDecision.ASK
    assert (
        evaluate_command(
            config,
            ["git", "diff", "--output=../outside.patch"],
            project_root=tmp_path,
        ).decision
        == PolicyDecision.DENY
    )
    assert (
        evaluate_command(
            config,
            ["pytest", "../outside/test_action.py"],
            project_root=tmp_path,
        ).decision
        == PolicyDecision.DENY
    )
    assert evaluate_command(config, ["npm", "install", "-g", "pkg"]).requires_human_approval
    assert evaluate_command(
        config,
        ["env", "NPM_CONFIG_GLOBAL=true", "npm", "install", "pkg"],
        project_root=tmp_path,
    ).requires_human_approval
    assert (
        evaluate_command(
            config,
            ["env", "NPM_CONFIG_PREFIX=../outside", "npm", "install", "pkg"],
            project_root=tmp_path,
        ).decision
        == PolicyDecision.DENY
    )
    assert (
        evaluate_command(
            config,
            ["env", "-C../outside", "ruff", "format", "."],
            project_root=tmp_path,
        ).decision
        == PolicyDecision.DENY
    )
    assert evaluate_command(
        config,
        ["git", "-c", "alias.inspect=!gh repo delete owner/repo", "inspect"],
        project_root=tmp_path,
    ).requires_human_approval
    assert evaluate_command(
        config,
        ["python", "-c", "print('opaque command')"],
        project_root=tmp_path,
    ).requires_human_approval
    exact_inline_sensor = ["python", "-c", "print('trusted sensor')"]
    config.policy.allow_execute.append(exact_inline_sensor)
    assert (
        evaluate_command(config, exact_inline_sensor, project_root=tmp_path).decision
        == PolicyDecision.ALLOW
    )
    exact_clustered_sensor = ["python3", "-Ic", "print('trusted sensor')"]
    config.policy.allow_execute.append(exact_clustered_sensor)
    assert (
        evaluate_command(config, exact_clustered_sensor, project_root=tmp_path).decision
        == PolicyDecision.ALLOW
    )
    exact_runner_sensor = ["npm", "exec", "--call", "printf trusted-metadata"]
    config.policy.allow_execute.append(exact_runner_sensor)
    assert evaluate_command(config, exact_runner_sensor, project_root=tmp_path).decision == (
        PolicyDecision.ALLOW
    )
    exact_opaque_sensor = ["opaque-interpreter", "-e", "print-safe-metadata"]
    config.policy.allow_execute.append(exact_opaque_sensor)
    assert evaluate_command(config, exact_opaque_sensor, project_root=tmp_path).decision == (
        PolicyDecision.ALLOW
    )
    assert evaluate_command(
        config,
        [str(tmp_path.parent / "git"), "status"],
        project_root=tmp_path,
    ).requires_human_approval
    assert (
        evaluate_path(config, tmp_path, Path("README.md"), write=True).decision
        == PolicyDecision.ALLOW
    )
    assert (
        evaluate_path(config, tmp_path, Path(".env.local"), write=True).decision
        == PolicyDecision.DENY
    )


def test_configuration_models_reject_empty_command_arguments() -> None:
    with pytest.raises(ValueError, match="command prefixes"):
        PolicyConfig(allow_execute=[[]])

    with pytest.raises(ValueError, match="command arguments"):
        ProjectConfig(
            name="sample",
            language="Python",
            build=[""],
            test=["pytest"],
            lint=["ruff"],
        )


def test_policy_blocks_ripgrep_subprocess_flags(tmp_path: Path) -> None:
    from hexaharness.config import default_config

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    config = default_config(tmp_path)

    for argv in (
        ["rg", "--pre", "processor", "needle", "."],
        ["rg", "--pre=processor", "needle", "."],
        ["rg", "--hostname-bin", "hostname-command", "needle", "."],
        ["rg", "--hostname-bin=hostname-command", "needle", "."],
        ["rg", "--search-zip", "needle", "."],
        ["rg", "-zH", "needle", "."],
    ):
        assert evaluate_command(config, argv, project_root=tmp_path).decision == PolicyDecision.DENY

    assert (
        evaluate_command(
            config, ["rg", "--no-search-zip", "needle", "."], project_root=tmp_path
        ).decision
        == PolicyDecision.ALLOW
    )


def test_policy_classifies_git_subcommands_behind_global_options(tmp_path: Path) -> None:
    from hexaharness.config import default_config

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    config = default_config(tmp_path)

    for argv in (
        ["git", "-C", ".", "send-pack", "origin", "HEAD:main"],
        ["git", "-C.", "push", "origin", "main"],
        ["git", "--git-dir=.git", "send-pack", "origin", "HEAD:main"],
        ["env", "git", "--no-pager", "send-pack", "origin", "HEAD:main"],
    ):
        outcome = evaluate_command(config, argv, project_root=tmp_path)
        assert outcome.decision == PolicyDecision.ASK
        assert outcome.requires_human_approval

    assert (
        evaluate_command(config, ["git", "-C", ".", "status"], project_root=tmp_path).decision
        == PolicyDecision.ALLOW
    )
    assert (
        evaluate_command(
            config, ["git", "--work-tree", ".", "reset", "--hard"], project_root=tmp_path
        ).decision
        == PolicyDecision.DENY
    )


def test_policy_checks_local_dependency_url_paths(tmp_path: Path) -> None:
    from hexaharness.config import default_config

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    inside = tmp_path / "packages" / "inside"
    inside.mkdir(parents=True)
    outside = tmp_path.parent / "outside-package"
    config = default_config(tmp_path)

    outside_uri = outside.resolve().as_uri()
    for locator in (
        outside_uri,
        outside_uri.replace("file://", "git+file://", 1),
        f"sample @ {outside_uri}",
        f"sample@{outside_uri}",
        "file:../outside-package",
        "sample@file:../outside-package",
        "link:../outside-package",
        "portal:../outside-package",
        "workspace:../outside-package",
        "patch:../outside-package#./fix.patch",
    ):
        assert (
            evaluate_command(config, ["npm", "install", locator], project_root=tmp_path).decision
            == PolicyDecision.DENY
        )

    assert (
        evaluate_command(
            config, ["npm", "install", inside.resolve().as_uri()], project_root=tmp_path
        ).decision
        == PolicyDecision.ALLOW
    )
    assert (
        evaluate_command(
            config, ["npm", "install", "https://example.test/package.tgz"], project_root=tmp_path
        ).decision
        == PolicyDecision.ALLOW
    )


def test_policy_gates_global_package_location_forms(tmp_path: Path) -> None:
    from hexaharness.config import default_config

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    config = default_config(tmp_path)

    for argv in (
        ["npm", "install", "--location=global", "package"],
        ["npm", "install", "--loc=global", "package"],
        ["npm", "install", "--location", "global", "package"],
        ["npm", "install", "--global=true", "package"],
        ["npm", "install", "-gD", "package"],
    ):
        outcome = evaluate_command(config, argv, project_root=tmp_path)
        assert outcome.decision == PolicyDecision.ASK
        assert outcome.requires_human_approval


def test_policy_does_not_treat_cloud_resource_names_as_read_only_operations(
    tmp_path: Path,
) -> None:
    from hexaharness.config import default_config

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    config = default_config(tmp_path)
    # Exercise precedence against deliberately broad repository policy rules: intrinsic external
    # mutation classification must still win over a configured allow prefix.
    config.policy.allow_execute.extend([["az"], ["gcloud"]])

    for argv in (
        ["az", "group", "delete", "--name", "show"],
        ["az", "role", "assignment", "create", "--assignee", "list"],
        ["gcloud", "compute", "instances", "delete", "show"],
        ["gcloud", "projects", "add-iam-policy-binding", "list"],
    ):
        outcome = evaluate_command(config, argv, project_root=tmp_path)
        assert outcome.decision == PolicyDecision.ASK
        assert outcome.requires_human_approval

    assert (
        evaluate_command(config, ["az", "version"], project_root=tmp_path).decision
        == PolicyDecision.ALLOW
    )
    assert (
        evaluate_command(config, ["gcloud", "info"], project_root=tmp_path).decision
        == PolicyDecision.ALLOW
    )
