from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hexaharness.cli import app
from hexaharness.config import load_config
from hexaharness.errors import PolicyBlockedError
from hexaharness.external_access import (
    _digest,
    external_path,
    grant_external_access,
    load_access_grant,
    revoke_external_access,
)
from hexaharness.external_transactions import identity, pair_state
from hexaharness.models import ExternalAccessRequest, PathOperation, utc_now
from hexaharness.policy import evaluate_command, evaluate_path
from hexaharness.runner import new_external_action_step, run_task_command
from hexaharness.state import (
    RECONCILE_EXTERNAL_ACTION,
    VERIFY_EXTERNAL_ACTION_RESULT,
    checkpoint_task,
    load_task,
    record_write_observation,
    stage_external_action,
    start_task,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="anchored-v1 needs POSIX ownership")


@pytest.fixture
def managed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    parent = tmp_path_factory.mktemp("transaction")
    parent.chmod(0o700)
    put(parent / "common.lock", "")
    return parent


def put(path: Path, text: str = "synthetic") -> None:
    path.write_text(text)
    path.chmod(0o600)


def scope_for(parent: Path, *, scratch: bool = True, pairs: bool = True) -> ExternalAccessRequest:
    transaction = {
        "parent": str(parent),
        "lock": "common.lock",
        "executor_contract": "anchored-v1",
        "scratch_directories": [
            {
                "name": ".probe-op1.scratch",
                "operations": ["stat", "mkdir", "rmdir"],
                "files": [
                    {"name": name, "operations": ["stat", "read", "create", "replace", "delete"]}
                    for name in ("first", "second", "moved")
                ],
            }
        ]
        if scratch
        else [],
        "hardlink_pairs": [
            {
                "source": {
                    "name": f"{kind}-op1.stage",
                    "operations": ["stat", "read", "create", "delete"],
                },
                "destination": {"name": f"{kind}.json", "operations": ["stat", "read", "link"]},
            }
            for kind in ("profile", "journal")
        ]
        if pairs
        else [],
    }
    return ExternalAccessRequest.model_validate(
        {
            "purpose": "Synthetic anchored transaction",
            "expires_at": utc_now() + timedelta(minutes=5),
            "paths": [{"path": str(parent / "common.lock"), "operations": ["read", "stat"]}],
            "transactions": [transaction],
            "arguments": [{"path": str(parent), "operation": "transaction"}],
        }
    )


def grant_for(root: Path, parent: Path, scope: ExternalAccessRequest | None = None):
    task = start_task(root, "Synthetic transaction")
    argv = ["python", "issuer.py", str(parent)]
    grant = grant_external_access(
        root, task.task_id, scope or scope_for(parent), approved=True, argv=argv
    )
    return task, argv, grant


def test_narrow_scope_paths_and_exact_command(harness_project: Path, managed: Path) -> None:
    task, argv, grant = grant_for(harness_project, managed)
    config = load_config(harness_project)
    for target, op, expected in [
        (managed / ".probe-op1.scratch", "mkdir", "allow"),
        (managed / ".probe-op1.scratch/first", "create", "allow"),
        (managed / ".probe-other.scratch", "mkdir", "deny"),
        (managed / ".probe-op1.scratch/other", "create", "deny"),
        (managed / ".probe-op1.scratch/nested/first", "create", "deny"),
        (managed / ".probe-op1.scratch", "delete", "deny"),
        (managed / "profile.json", "link", "deny"),
    ]:
        outcome = evaluate_path(
            config,
            harness_project,
            target,
            write=False,
            operation=PathOperation(op),
            task_id=task.task_id,
            access=grant,
        )
        assert outcome.decision == expected, outcome.reason
    assert (
        evaluate_command(
            config, argv, project_root=harness_project, task_id=task.task_id, access=grant
        ).decision
        == "ask"
    )
    assert (
        evaluate_command(
            config,
            [*argv, "different"],
            project_root=harness_project,
            task_id=task.task_id,
            access=grant,
        ).decision
        == "deny"
    )


@pytest.mark.parametrize(
    "name", ["*", "probe-*", "../probe", "a/b", "a\\b", "owner.vault", ".ssh", "conversations"]
)
def test_unsafe_scratch_names_rejected(harness_project: Path, managed: Path, name: str) -> None:
    scope = scope_for(managed)
    scope.transactions[0].scratch_directories[0].name = name
    with pytest.raises(ValueError):
        grant_for(harness_project, managed, scope)


@pytest.mark.parametrize(
    "fault", ["third", "unknown", "different", "symlink", "mode", "outside", "duplicate"]
)
def test_pair_topology_fail_closed(harness_project: Path, managed: Path, fault: str) -> None:
    source, destination = managed / "profile-op1.stage", managed / "profile.json"
    put(source)
    if fault == "symlink":
        destination.symlink_to(source)
    elif fault == "different":
        put(destination)
    elif fault == "unknown":
        os.link(source, managed / "unknown")
    else:
        os.link(source, destination)
    if fault == "third":
        os.link(source, managed / "third")
    if fault == "mode":
        source.chmod(0o644)
    scope = scope_for(managed, scratch=False)
    if fault == "outside":
        scope.transactions[0].hardlink_pairs[0].destination.name = "../other"
    if fault == "duplicate":
        scope.transactions[0].hardlink_pairs[0].destination.name = "profile-op1.stage"
    with pytest.raises(ValueError):
        grant_for(harness_project, managed, scope)
    assert source.exists()


def test_pair_transitions_and_default_rejection(harness_project: Path, managed: Path) -> None:
    task, _, grant = grant_for(harness_project, managed, scope_for(managed, scratch=False))
    config = load_config(harness_project)
    pair = grant.scope.transactions[0].hardlink_pairs[0]
    source, destination = managed / pair.source.name, managed / pair.destination.name
    assert pair_state(managed, pair) == "absent"
    put(source)
    assert pair_state(managed, pair) == "source-only"
    assert (
        evaluate_path(
            config,
            harness_project,
            destination,
            write=True,
            operation=PathOperation.LINK,
            task_id=task.task_id,
            access=grant,
        ).decision
        == "allow"
    )
    os.link(source, destination)
    assert pair_state(managed, pair) == "paired"
    with pytest.raises(ValueError, match="multiply"):
        external_path(source)
    assert (
        evaluate_path(
            config,
            harness_project,
            destination,
            write=False,
            operation=PathOperation.READ,
            task_id=task.task_id,
            access=grant,
        ).decision
        == "allow"
    )
    assert (
        evaluate_path(
            config,
            harness_project,
            destination,
            write=True,
            operation=PathOperation.LINK,
            task_id=task.task_id,
            access=grant,
        ).decision
        == "deny"
    )
    source.unlink()
    assert pair_state(managed, pair) == "destination-only"
    os.link(destination, managed / "unknown")
    assert (
        evaluate_path(
            config,
            harness_project,
            destination,
            write=False,
            operation=PathOperation.READ,
            task_id=task.task_id,
            access=grant,
        ).decision
        == "deny"
    )


@pytest.mark.parametrize("fault", ["parent", "lock", "scratch", "symlink", "permissions", "owner"])
def test_bound_metadata_changes_block_commands(
    harness_project: Path, managed: Path, fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    task, argv, grant = grant_for(harness_project, managed)
    if fault == "parent":
        managed.rename(managed.with_name(managed.name + "-original"))
        managed.mkdir(mode=0o700)
        put(managed / "common.lock", "")
    elif fault == "lock":
        (managed / "common.lock").rename(managed / "old.lock")
        put(managed / "common.lock", "")
    elif fault == "scratch":
        (managed / ".probe-op1.scratch").mkdir(mode=0o700)
    elif fault == "symlink":
        (managed / ".probe-op1.scratch").symlink_to(managed, target_is_directory=True)
    elif fault == "permissions":
        managed.chmod(0o755)
    else:
        monkeypatch.setattr(os, "geteuid", lambda: managed.stat().st_uid + 1)
    result = evaluate_command(
        load_config(harness_project),
        argv,
        project_root=harness_project,
        task_id=task.task_id,
        access=grant,
    )
    assert result.decision == "deny"


def test_scratch_recovery_separates_inspection_and_cleanup(
    harness_project: Path, managed: Path
) -> None:
    directory = managed / ".probe-op1.scratch"
    directory.mkdir(mode=0o700)
    put(directory / "first")
    with pytest.raises(ValueError, match="collision"):
        grant_for(harness_project, managed)
    scope = scope_for(managed, pairs=False)
    scratch = scope.transactions[0].scratch_directories[0]
    scratch.operations = [PathOperation.STAT, PathOperation.LIST]
    for child in scratch.files:
        child.operations = [PathOperation.STAT, PathOperation.READ]
    task, _, inspection = grant_for(harness_project, managed, scope)
    config = load_config(harness_project)
    assert (
        evaluate_path(
            config,
            harness_project,
            directory / "first",
            write=False,
            operation=PathOperation.READ,
            task_id=task.task_id,
            access=inspection,
        ).decision
        == "allow"
    )
    assert (
        evaluate_path(
            config,
            harness_project,
            directory / "first",
            write=True,
            operation=PathOperation.DELETE,
            task_id=task.task_id,
            access=inspection,
        ).decision
        == "deny"
    )
    scratch.operations.append(PathOperation.RMDIR)
    scratch.files[0].operations.append(PathOperation.DELETE)
    with pytest.raises(ValueError, match="provenance"):
        grant_for(harness_project, managed, scope)
    scratch.created_identity = identity(directory)
    scratch.recovery_evidence = "creation.json"
    (harness_project / "creation.json").write_text(
        json.dumps(scratch.created_identity.model_dump())
    )
    recovery_task, _, cleanup = grant_for(harness_project, managed, scope)
    assert (
        evaluate_path(
            config,
            harness_project,
            directory,
            write=True,
            operation=PathOperation.RMDIR,
            task_id=recovery_task.task_id,
            access=cleanup,
        ).decision
        == "deny"
    )
    (directory / "first").unlink()
    assert (
        evaluate_path(
            config,
            harness_project,
            directory,
            write=True,
            operation=PathOperation.RMDIR,
            task_id=recovery_task.task_id,
            access=cleanup,
        ).decision
        == "allow"
    )
    put(directory / "foreign")
    assert (
        evaluate_path(
            config,
            harness_project,
            directory,
            write=True,
            operation=PathOperation.RMDIR,
            task_id=recovery_task.task_id,
            access=cleanup,
        ).decision
        == "deny"
    )
    assert (directory / "foreign").exists()


@pytest.mark.parametrize("kind", ["scratch", "child", "pair"])
def test_denied_names_apply_without_explicit_operands(
    harness_project: Path, managed: Path, kind: str
) -> None:
    scope = scope_for(managed)
    txn = scope.transactions[0]
    if kind == "scratch":
        txn.scratch_directories[0].name = "secret-work"
    elif kind == "child":
        txn.scratch_directories[0].files[0].name = ".env"
    else:
        txn.hardlink_pairs[0].destination.name = "credentials.json"
    scope.arguments = []
    task = start_task(harness_project, "Check indirect denied writes")
    argv = ["python", "issuer.py"]
    grant = grant_external_access(harness_project, task.task_id, scope, approved=True, argv=argv)
    outcome = evaluate_command(
        load_config(harness_project),
        argv,
        project_root=harness_project,
        task_id=task.task_id,
        access=grant,
    )
    assert outcome.decision == "deny"
    assert "denied write" in outcome.reason


def test_legacy_signature_and_new_lifecycle(harness_project: Path, managed: Path) -> None:
    scope = scope_for(managed)
    legacy = scope.model_copy(deep=True)
    legacy.transactions = []
    legacy.arguments = []
    task = start_task(harness_project, "Legacy signature")
    grant = grant_external_access(harness_project, task.task_id, legacy, approved=True)
    payload = grant.model_dump(mode="json", exclude={"signature", "transaction_identities"})
    payload["scope"].pop("transactions")
    assert grant.signature == _digest(harness_project, "external-access-v1", payload)
    assert load_access_grant(harness_project, task.task_id, grant.grant_id) == grant
    task, argv, fresh = grant_for(harness_project, managed)
    stage_external_action(
        harness_project,
        task.task_id,
        pending_step=new_external_action_step(harness_project, task.task_id, argv, access=fresh),
    )
    second = grant_external_access(harness_project, task.task_id, scope, approved=True, argv=argv)
    with pytest.raises(PolicyBlockedError):
        run_task_command(
            harness_project,
            load_config(harness_project),
            task_id=task.task_id,
            argv=argv,
            approved=True,
            access_grant=second.grant_id,
        )
    second.scope.transactions[0].hardlink_pairs[0].source.name = "swapped"
    assert (
        evaluate_command(
            load_config(harness_project),
            argv,
            project_root=harness_project,
            task_id=task.task_id,
            access=second,
        ).decision
        == "deny"
    )
    revoke_external_access(harness_project, task.task_id, fresh.grant_id)
    with pytest.raises(PolicyBlockedError, match="revoked"):
        load_access_grant(harness_project, task.task_id, fresh.grant_id)


def test_cli_mkdir_query_is_explicit(harness_project: Path, managed: Path) -> None:
    task, _, grant = grant_for(harness_project, managed)
    result = CliRunner().invoke(
        app,
        [
            "policy-check",
            "--root",
            str(harness_project),
            "--task-id",
            task.task_id,
            "--access-grant",
            grant.grant_id,
            "--path",
            str(managed / ".probe-op1.scratch"),
            "--operation",
            "mkdir",
            "--write",
        ],
    )
    assert result.exit_code == 0, result.output


def test_external_symlink_into_project_does_not_skip_external_policy(
    harness_project: Path, managed: Path
) -> None:
    (managed / "alias").symlink_to(harness_project / "pyproject.toml")
    assert (
        evaluate_path(
            load_config(harness_project), harness_project, managed / "alias", write=False
        ).decision
        == "deny"
    )


@pytest.mark.parametrize(
    "stop",
    ["none", "probe", "unsupported", "foreign", "journal", "profile", "profile-crash", "published"],
)
def test_reviewed_executor_and_failure_receipts(
    harness_project: Path, managed: Path, stop: str
) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("synthetic executor uses Linux renameat2 for no-replace/exchange")
    fixture = Path(__file__).parent / "fixtures/transaction_executor.py"
    (harness_project / "issuer.py").write_text(fixture.read_text())
    put(managed / "registry.json", "{}")
    scope = scope_for(managed)
    scope.paths.append(
        type(scope.paths[0])(path=str(managed / "registry.json"), operations=["read", "replace"])
    )
    scope.paths.append(
        type(scope.paths[0])(
            path=str(managed / "registry-next.json"), operations=["create", "delete"]
        )
    )
    task = start_task(harness_project, "Run synthetic enrollment")
    argv = [sys.executable, "issuer.py", str(managed), stop]
    grant = grant_external_access(harness_project, task.task_id, scope, approved=True, argv=argv)
    stage_external_action(
        harness_project,
        task.task_id,
        pending_step=new_external_action_step(harness_project, task.task_id, argv, access=grant),
    )
    result = run_task_command(
        harness_project,
        load_config(harness_project),
        task_id=task.task_id,
        argv=argv,
        approved=True,
        access_grant=grant.grant_id,
    )
    state = load_task(harness_project, task.task_id)
    marker = VERIFY_EXTERNAL_ACTION_RESULT if stop == "none" else RECONCILE_EXTERNAL_ACTION
    assert state.next_step and state.next_step.startswith(marker)
    assert (result.exit_code == 0) == (stop == "none")
    if stop in {"probe", "unsupported", "foreign"}:
        assert (managed / "registry.json").read_text() == "{}"
        assert not (managed / "profile.json").exists()
    elif stop == "none":
        profile = json.loads((managed / "profile.json").read_text())
        registry = json.loads((managed / "registry.json").read_text())
        assert profile["id"] == registry["pending"] == "op1"
        assert (managed / "profile.json").stat().st_nlink == 1
        assert not (managed / ".probe-op1.scratch").exists()
    else:
        # A new read-only grant can inspect a valid legacy or interrupted two-name topology.
        inspection = scope_for(managed, scratch=False)
        for pair in inspection.transactions[0].hardlink_pairs:
            pair.source.operations = pair.destination.operations = [
                PathOperation.STAT,
                PathOperation.READ,
            ]
        inspect_argv = ["python", "inspect.py", str(managed)]
        observed = grant_external_access(
            harness_project, task.task_id, inspection, approved=True, argv=inspect_argv
        )
        assert (
            evaluate_command(
                load_config(harness_project),
                inspect_argv,
                project_root=harness_project,
                task_id=task.task_id,
                access=observed,
            ).decision
            == "ask"
        )
    receipt = (harness_project / "receipt.json").read_text()
    assert "SYNTHETIC_ENROLLMENT_SECRET" not in receipt
    for file in (harness_project / ".hexaharness").rglob("*"):
        if file.is_file() and file.suffix in {".log", ".json", ".jsonl"}:
            assert "SYNTHETIC_ENROLLMENT_SECRET" not in file.read_text()
    if stop in {"profile", "profile-crash", "published"}:
        # Independent observations resolve uncertainty before a separately bound recovery write.
        evidence = Path("reconciliation.json")
        record_write_observation(harness_project, task.task_id, evidence)
        (harness_project / evidence).write_text(
            json.dumps(
                {
                    "journal": pair_state(managed, scope.transactions[0].hardlink_pairs[1]),
                    "profile": pair_state(managed, scope.transactions[0].hardlink_pairs[0]),
                    "phase": json.loads(receipt)["phase"],
                }
            )
        )
        checkpoint_task(
            harness_project,
            task.task_id,
            completed_step="Inspected paired synthetic state",
            artifacts=[str(evidence)],
            resolve_external_action=True,
        )
        recovery = scope.model_copy(deep=True)
        recovery.transactions[0].scratch_directories = []
        for pair in recovery.transactions[0].hardlink_pairs:
            pair.source.operations = [PathOperation.STAT, PathOperation.READ, PathOperation.DELETE]
            pair.destination.operations = [PathOperation.STAT, PathOperation.READ]
        recovery_argv = [sys.executable, "issuer.py", str(managed), "recover"]
        recovery_grant = grant_external_access(
            harness_project, task.task_id, recovery, approved=True, argv=recovery_argv
        )
        stage_external_action(
            harness_project,
            task.task_id,
            pending_step=new_external_action_step(
                harness_project, task.task_id, recovery_argv, access=recovery_grant
            ),
        )
        recovered = run_task_command(
            harness_project,
            load_config(harness_project),
            task_id=task.task_id,
            argv=recovery_argv,
            approved=True,
            access_grant=recovery_grant.grant_id,
        )
        assert recovered.exit_code == 0
        assert (managed / "profile.json").stat().st_nlink == 1
        assert json.loads((managed / "registry.json").read_text())["pending"] == "op1"
        assert "SYNTHETIC_ENROLLMENT_SECRET" not in (harness_project / "receipt.json").read_text()
        final_receipt = json.loads((harness_project / "receipt.json").read_text())
        assert final_receipt["parent_identity"] == list(identity(managed).model_dump().values())
        assert final_receipt["lock_identity"] == list(
            identity(managed / "common.lock").model_dump().values()
        )
        for file in (harness_project / ".hexaharness").rglob("*"):
            if file.is_file() and file.suffix in {".log", ".json", ".jsonl"}:
                assert "SYNTHETIC_ENROLLMENT_SECRET" not in file.read_text()
