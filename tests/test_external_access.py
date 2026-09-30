from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hexaharness.cli import app
from hexaharness.config import load_config
from hexaharness.errors import PolicyBlockedError
from hexaharness.external_access import (
    grant_external_access,
    load_access_grant,
    revoke_external_access,
)
from hexaharness.models import ExternalAccessRequest, PathOperation, PolicyDecision, utc_now
from hexaharness.policy import evaluate_command, evaluate_path
from hexaharness.runner import new_external_action_step, run_task_command
from hexaharness.state import (
    RECONCILE_EXTERNAL_ACTION,
    checkpoint_task,
    load_task,
    record_write_observation,
    save_task,
    stage_external_action,
    start_task,
)
from hexaharness.workflow import resume_task


@pytest.fixture
def operational_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("operational")
    (root / "config.json").write_text('{"relay": "https://example.invalid", "key": "signing.key"}')
    (root / "signing.key").write_text("FILE_ONLY_SECRET_SENTINEL")
    (root / "registration.json").write_text("{}")
    (root / "owner.vault").write_text("PROTECTED_VAULT_SENTINEL")
    return root


def request_for(
    path: Path,
    operations: list[str],
    *,
    children: list[str] | None = None,
    argument: str | None = None,
) -> ExternalAccessRequest:
    return ExternalAccessRequest.model_validate(
        {
            "purpose": "Synthetic operational workflow",
            "expires_at": (utc_now() + timedelta(minutes=10)).isoformat(),
            "paths": [{"path": str(path), "operations": operations, "children": children or []}],
            "arguments": [{"path": str(path), "operation": argument}] if argument else [],
        }
    )


@pytest.mark.parametrize("child", [".env", "secret-*.tmp", "registration-stage-*.tmp"])
def test_transaction_cannot_bypass_denied_write_children(
    harness_project: Path, operational_dir: Path, child: str
) -> None:
    task = start_task(harness_project, "Preserve denied transaction targets")
    if child == "registration-stage-*.tmp":
        (operational_dir / "registration-stage-secret.tmp").write_text("synthetic")
    argv = ["python", "issuer.py", str(operational_dir)]
    grant = grant_external_access(
        harness_project,
        task.task_id,
        request_for(operational_dir, ["create"], children=[child], argument="transaction"),
        approved=True,
        argv=argv,
    )
    outcome = evaluate_command(
        load_config(harness_project),
        argv,
        project_root=harness_project,
        task_id=task.task_id,
        access=grant,
    )
    assert outcome.decision == PolicyDecision.DENY
    assert "denied write" in outcome.reason


def test_read_only_grant_cannot_downgrade_publication_or_unknown_deny(
    harness_project: Path, operational_dir: Path
) -> None:
    task = start_task(harness_project, "Preserve command gates")
    config = load_config(harness_project)
    argv = ["git", "push", "origin", "HEAD:main"]
    scope = request_for(operational_dir / "config.json", ["read"])
    grant = grant_external_access(harness_project, task.task_id, scope, approved=True, argv=argv)
    with pytest.raises(PolicyBlockedError, match="one-time binding"):
        run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=argv,
            approved=True,
            access_grant=grant.grant_id,
        )
    config.policy.unknown_execute = PolicyDecision.DENY
    unknown = ["unrecognized-inspector"]
    second = grant_external_access(
        harness_project, task.task_id, scope, approved=True, argv=unknown
    )
    assert (
        evaluate_command(
            config, unknown, project_root=harness_project, task_id=task.task_id, access=second
        ).decision
        == PolicyDecision.DENY
    )


def test_staged_action_cannot_swap_grants(harness_project: Path, operational_dir: Path) -> None:
    task = start_task(harness_project, "Bind transaction scope")
    target = operational_dir / "profile-new.json"
    argv = ["python", "issuer.py", str(target)]
    scope = request_for(target, ["create"], argument="create")
    first = grant_external_access(harness_project, task.task_id, scope, approved=True, argv=argv)
    second = grant_external_access(harness_project, task.task_id, scope, approved=True, argv=argv)
    stage_external_action(
        harness_project,
        task.task_id,
        pending_step=new_external_action_step(harness_project, task.task_id, argv, access=first),
    )
    with pytest.raises(PolicyBlockedError):
        run_task_command(
            harness_project,
            load_config(harness_project),
            task_id=task.task_id,
            argv=argv,
            approved=True,
            access_grant=second.grant_id,
        )
    assert not target.exists()


def test_running_mutation_is_bounded_by_grant_expiry(
    harness_project: Path, operational_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = start_task(harness_project, "Bound issuance execution")
    (harness_project / "slow.py").write_text("import time\ntime.sleep(5)\n")
    target = operational_dir / "profile-slow.json"
    argv = [sys.executable, "slow.py", str(target)]
    scope = request_for(target, ["create"], argument="create")
    grant = grant_external_access(harness_project, task.task_id, scope, approved=True, argv=argv)
    stage_external_action(
        harness_project,
        task.task_id,
        pending_step=new_external_action_step(harness_project, task.task_id, argv, access=grant),
    )
    monkeypatch.setattr(
        "hexaharness.runner.utc_now", lambda: grant.scope.expires_at - timedelta(milliseconds=100)
    )
    result = run_task_command(
        harness_project,
        load_config(harness_project),
        task_id=task.task_id,
        argv=argv,
        approved=True,
        access_grant=grant.grant_id,
    )
    assert result.timed_out
    state = load_task(harness_project, task.task_id)
    assert state.next_step and state.next_step.startswith(RECONCILE_EXTERNAL_ACTION)


def test_external_access_is_closed_by_default(harness_project: Path, operational_dir: Path) -> None:
    config = load_config(harness_project)
    target = operational_dir / "config.json"
    config.policy.read_paths = ["**/*", str(operational_dir / "*")]
    assert (
        evaluate_path(config, harness_project, target, write=False).decision == PolicyDecision.DENY
    )
    assert (
        evaluate_command(
            config, ["python", "inspect.py", "--config", str(target)], project_root=harness_project
        ).decision
        == PolicyDecision.DENY
    )


def test_grant_needs_approval_and_is_task_operation_and_file_bound(
    harness_project: Path, operational_dir: Path
) -> None:
    config = load_config(harness_project)
    task = start_task(harness_project, "Inspect settings")
    target = operational_dir / "config.json"
    scope = request_for(target, ["read", "stat"])
    with pytest.raises(PolicyBlockedError, match="approval"):
        grant_external_access(harness_project, task.task_id, scope, approved=False)
    grant = grant_external_access(harness_project, task.task_id, scope, approved=True)

    def decision(
        path: Path, operation: PathOperation, task_id: str = task.task_id
    ) -> PolicyDecision:
        return evaluate_path(
            config,
            harness_project,
            path,
            write=False,
            operation=operation,
            task_id=task_id,
            access=grant,
        ).decision

    assert decision(target, PathOperation.READ) == PolicyDecision.ALLOW
    assert decision(target, PathOperation.STAT) == PolicyDecision.ALLOW
    assert decision(target, PathOperation.REPLACE) == PolicyDecision.DENY
    assert decision(operational_dir / "signing.key", PathOperation.READ) == PolicyDecision.DENY
    assert decision(operational_dir / "owner.vault", PathOperation.READ) == PolicyDecision.DENY
    other = start_task(harness_project, "Unrelated task")
    assert decision(target, PathOperation.READ, other.task_id) == PolicyDecision.DENY
    current = load_task(harness_project, task.task_id)
    current.external_access = []
    with pytest.raises(PolicyBlockedError, match="approval lifecycle"):
        save_task(harness_project, current)


def test_metadata_listing_does_not_grant_file_contents(
    harness_project: Path, operational_dir: Path
) -> None:
    task = start_task(harness_project, "Inspect directory metadata")
    grant = grant_external_access(
        harness_project, task.task_id, request_for(operational_dir, ["stat", "list"]), approved=True
    )
    config = load_config(harness_project)
    for operation in (PathOperation.STAT, PathOperation.LIST):
        assert (
            evaluate_path(
                config,
                harness_project,
                operational_dir,
                write=False,
                operation=operation,
                task_id=task.task_id,
                access=grant,
            ).decision
            == PolicyDecision.ALLOW
        )
    assert (
        evaluate_path(
            config,
            harness_project,
            operational_dir / "config.json",
            write=False,
            task_id=task.task_id,
            access=grant,
        ).decision
        == PolicyDecision.DENY
    )


@pytest.mark.parametrize(
    "name", ["*", "*.json", "../file", "nested/file", "profile**", "owner.vault"]
)
def test_transaction_names_cannot_open_unbounded_or_protected_targets(
    harness_project: Path, operational_dir: Path, name: str
) -> None:
    task = start_task(harness_project, "Constrain transaction names")
    with pytest.raises(ValueError):
        grant_external_access(
            harness_project,
            task.task_id,
            request_for(operational_dir, ["create"], children=[name]),
            approved=True,
            argv=["issuer"],
        )


@pytest.mark.parametrize("target", ["/", "/Users", "home", "project"])
def test_broad_roots_cannot_be_granted(harness_project: Path, target: str) -> None:
    path = (
        Path.home()
        if target == "home"
        else harness_project
        if target == "project"
        else Path(target)
    )
    task = start_task(harness_project, "Reject broad authority")
    with pytest.raises(ValueError):
        grant_external_access(
            harness_project, task.task_id, request_for(path, ["stat", "list"]), approved=True
        )


def test_expiry_revocation_and_tampering_fail_closed(
    harness_project: Path, operational_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = start_task(harness_project, "Bound grant lifetime")
    scope = request_for(operational_dir / "config.json", ["read"])
    grant = grant_external_access(harness_project, task.task_id, scope, approved=True)
    with monkeypatch.context() as clock:
        clock.setattr("hexaharness.external_access.utc_now", lambda: scope.expires_at)
        with pytest.raises(PolicyBlockedError, match="expired"):
            load_access_grant(harness_project, task.task_id, grant.grant_id)
    revoke_external_access(harness_project, task.task_id, grant.grant_id)
    with pytest.raises(PolicyBlockedError, match="revoked"):
        load_access_grant(harness_project, task.task_id, grant.grant_id)
    second = grant_external_access(harness_project, task.task_id, scope, approved=True)
    state_file = harness_project / ".hexaharness" / "state" / f"{task.task_id}.json"
    raw = json.loads(state_file.read_text())
    raw["external_access"][-1]["scope"]["paths"][0]["operations"].append("replace")
    state_file.write_text(json.dumps(raw))
    with pytest.raises(PolicyBlockedError, match="binding"):
        load_access_grant(harness_project, task.task_id, second.grant_id)


def test_alias_and_traversal_do_not_extend_a_grant(
    harness_project: Path, operational_dir: Path
) -> None:
    task = start_task(harness_project, "Reject path aliases")
    config = load_config(harness_project)
    target = operational_dir / "config.json"
    scope = request_for(target, ["read"])
    grant = grant_external_access(harness_project, task.task_id, scope, approved=True)
    alias = operational_dir / "alias.json"
    alias.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        grant_external_access(
            harness_project, task.task_id, request_for(alias, ["read"]), approved=True
        )
    target.unlink()
    target.symlink_to(operational_dir / "owner.vault")
    assert (
        evaluate_path(
            config, harness_project, target, write=False, task_id=task.task_id, access=grant
        ).decision
        == PolicyDecision.DENY
    )


def test_exact_command_and_declared_operations_cover_split_and_attached_paths(
    harness_project: Path, operational_dir: Path
) -> None:
    task = start_task(harness_project, "Inspect exact settings")
    config = load_config(harness_project)
    target = operational_dir / "config.json"
    for argv in (
        ["python", "inspect.py", "--config", str(target)],
        ["python", "inspect.py", f"--config={target}"],
    ):
        grant = grant_external_access(
            harness_project,
            task.task_id,
            request_for(target, ["read"], argument="read"),
            approved=True,
            argv=argv,
        )
        assert (
            evaluate_command(
                config, argv, project_root=harness_project, task_id=task.task_id, access=grant
            ).decision
            == PolicyDecision.ASK
        )
        assert (
            evaluate_command(
                config,
                [*argv, "--changed"],
                project_root=harness_project,
                task_id=task.task_id,
                access=grant,
            ).decision
            == PolicyDecision.DENY
        )
    with pytest.raises(ValueError, match="outside scope"):
        grant_external_access(
            harness_project,
            task.task_id,
            request_for(target, ["read"], argument="replace"),
            approved=True,
            argv=["writer", str(target)],
        )
    forbidden = ["rm", "-rf", str(target)]
    grant = grant_external_access(
        harness_project,
        task.task_id,
        request_for(target, ["read"], argument="read"),
        approved=True,
        argv=forbidden,
    )
    assert (
        evaluate_command(
            config, forbidden, project_root=harness_project, task_id=task.task_id, access=grant
        ).decision
        == PolicyDecision.DENY
    )


def test_read_command_discards_file_secrets_before_logging(
    harness_project: Path, operational_dir: Path
) -> None:
    task = start_task(harness_project, "Use key internally")
    script = harness_project / "inspect.py"
    script.write_text(
        "import pathlib,sys\nvalue=pathlib.Path(sys.argv[1]).read_text()\n"
        "print(value)\nprint(value,file=sys.stderr)\n"
    )
    key = operational_dir / "signing.key"
    argv = [sys.executable, "inspect.py", str(key)]
    grant = grant_external_access(
        harness_project,
        task.task_id,
        request_for(key, ["read"], argument="read"),
        approved=True,
        argv=argv,
    )
    result = run_task_command(
        harness_project,
        load_config(harness_project),
        task_id=task.task_id,
        argv=argv,
        approved=True,
        access_grant=grant.grant_id,
    )
    assert result.exit_code == 0
    assert "suppressed" in result.summary
    for file in (harness_project / ".hexaharness").rglob("*"):
        if file.is_file() and file.suffix in {".json", ".jsonl", ".log"}:
            assert "FILE_ONLY_SECRET_SENTINEL" not in file.read_text()
    assert load_task(harness_project, task.task_id).next_step is None


ISSUER = """import json, pathlib, sys
root=pathlib.Path(sys.argv[1])
lock=root/"registration.lock"
journal=root/"registration.journal"
temporary=root/"registration-stage-123.tmp"
profile=root/"profile-person.json"
lock.write_text("locked")
journal.write_text("issuing")
temporary.write_text(json.dumps({"pending": "person"}))
temporary.replace(root/"registration.json")
profile.write_text(json.dumps({"token": "PROFILE_SECRET_SENTINEL", "expires_in_hours": 24}))
print("PROFILE_SECRET_SENTINEL")
if "--fail" in sys.argv: raise SystemExit(1)
journal.unlink()
lock.unlink()
"""


@pytest.mark.parametrize("fail", [False, True])
def test_issuance_transaction_and_uncertain_result_recovery(
    harness_project: Path, operational_dir: Path, fail: bool
) -> None:
    task = start_task(harness_project, "Issue one profile")
    (harness_project / "issuer.py").write_text(ISSUER)
    argv = [sys.executable, "issuer.py", str(operational_dir)] + (["--fail"] if fail else [])
    scope = request_for(
        operational_dir,
        ["read", "stat", "create", "replace", "delete"],
        children=[
            "registration.json",
            "registration.lock",
            "registration.journal",
            "registration-stage-*.tmp",
            "profile-person.json",
        ],
        argument="transaction",
    )
    grant = grant_external_access(harness_project, task.task_id, scope, approved=True, argv=argv)
    config = load_config(harness_project)
    stage_external_action(
        harness_project,
        task.task_id,
        pending_step=new_external_action_step(harness_project, task.task_id, argv, access=grant),
    )
    with pytest.raises(PolicyBlockedError, match="retried"):
        run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=argv,
            approved=True,
            retries=1,
            access_grant=grant.grant_id,
        )
    result = run_task_command(
        harness_project,
        config,
        task_id=task.task_id,
        argv=argv,
        approved=True,
        access_grant=grant.grant_id,
    )
    assert result.exit_code == (1 if fail else 0)
    assert (
        json.loads((operational_dir / "profile-person.json").read_text())["expires_in_hours"] == 24
    )
    assert json.loads((operational_dir / "registration.json").read_text()) == {"pending": "person"}
    assert (operational_dir / "registration.journal").exists() == fail
    state = load_task(harness_project, task.task_id)
    assert state.next_step
    assert state.next_step.startswith(
        RECONCILE_EXTERNAL_ACTION if fail else "VERIFY_EXTERNAL_ACTION_RESULT"
    )
    with pytest.raises(PolicyBlockedError):
        run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=argv,
            approved=True,
            access_grant=grant.grant_id,
        )
    if fail:
        resume_task(harness_project, task.task_id, reactivate=True)
        profile = operational_dir / "profile-person.json"
        registry = operational_dir / "registration.json"
        (harness_project / "reconcile.py").write_text(
            "import json,pathlib,sys\n"
            "assert json.loads(pathlib.Path(sys.argv[1]).read_text())['expires_in_hours']==24\n"
            "assert json.loads(pathlib.Path(sys.argv[2]).read_text())['pending']=='person'\n"
        )
        inspect_argv = [sys.executable, "reconcile.py", str(profile), str(registry)]
        read_scope = request_for(profile, ["read"], argument="read")
        registry_scope = request_for(registry, ["read"], argument="read")
        read_scope.paths.extend(registry_scope.paths)
        read_scope.arguments.extend(registry_scope.arguments)
        inspector = grant_external_access(
            harness_project, task.task_id, read_scope, approved=True, argv=inspect_argv
        )
        inspected = run_task_command(
            harness_project,
            config,
            task_id=task.task_id,
            argv=inspect_argv,
            approved=True,
            access_grant=inspector.grant_id,
        )
        assert inspected.exit_code == 0
        assert load_task(harness_project, task.task_id).next_step == state.next_step
    receipt = f".hexaharness/artifacts/{task.task_id}/receipt.json"
    record_write_observation(harness_project, task.task_id, receipt)
    (harness_project / receipt).write_text(
        '{"observed_profile": true, "observed_pending_registration": true}'
    )
    recovered = checkpoint_task(
        harness_project,
        task.task_id,
        completed_step="Reconciled actual issuance state",
        artifacts=[receipt],
        clear_next=not fail,
        resolve_external_action=fail,
    )
    assert recovered.next_step is None
    assert "PROFILE_SECRET_SENTINEL" not in (harness_project / result.output_path).read_text()


def test_grant_cli_does_not_read_external_contents_or_hash_external_writes(
    harness_project: Path, operational_dir: Path
) -> None:
    runner = CliRunner()
    task = start_task(harness_project, "Check external output path")
    output = operational_dir / "delivery.zip"
    scope = request_for(output, ["create"], argument="create")
    (harness_project / "scope.json").write_text(scope.model_dump_json())
    args = ["grant-external", task.task_id, "--root", str(harness_project), "--scope", "scope.json"]
    assert runner.invoke(app, [*args, "--", "zipper", str(output)]).exit_code != 0
    granted = runner.invoke(app, [*args, "--approved", "--", "zipper", str(output)])
    assert granted.exit_code == 0, granted.output
    grant_id = json.loads(granted.output)["grant_id"]
    check = [
        "policy-check",
        "--root",
        str(harness_project),
        "--task-id",
        task.task_id,
        "--access-grant",
        grant_id,
        "--path",
        str(output),
    ]
    result = runner.invoke(app, [*check, "--operation", "create"])
    assert result.exit_code == 0, result.output
    assert "write_observation_id" not in json.loads(result.output)
    assert runner.invoke(app, [*check, "--write"]).exit_code == 2
    output.write_text("existing delivery")
    assert runner.invoke(app, [*check, "--operation", "create"]).exit_code == 2
    assert (
        runner.invoke(
            app, ["revoke-external", task.task_id, grant_id, "--root", str(harness_project)]
        ).exit_code
        == 0
    )
