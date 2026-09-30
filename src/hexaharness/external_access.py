"""Narrow, task-bound external path exceptions; never an operating-system sandbox."""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import os
import secrets
import stat
from datetime import timedelta
from pathlib import Path

from hexaharness.errors import PolicyBlockedError
from hexaharness.events import record_event
from hexaharness.io import redact_argv
from hexaharness.models import (
    ExternalAccessGrant,
    ExternalAccessRequest,
    PathOperation,
    TaskStatus,
    utc_now,
)
from hexaharness.paths import HarnessPaths
from hexaharness.state import _save_task_unlocked, harness_lock, load_task, task_lock

WRITE_OPERATIONS = {PathOperation.CREATE, PathOperation.REPLACE, PathOperation.DELETE}
PROTECTED_COMPONENTS = {".git", ".ssh", "owner.vault", "conversations", "chats"}


def binding_key(project_root: Path) -> bytes:
    """Reuse the owner-private key for domain-separated approval bindings."""
    key_path = HarnessPaths(project_root).external_action_key
    with harness_lock(project_root):
        if key_path.is_file():
            if os.name != "nt" and stat.S_IMODE(key_path.stat().st_mode) & 0o077:
                raise ValueError("external-action binding key permissions must be owner-only")
            key = key_path.read_bytes()
            if len(key) != 32:
                raise ValueError("external-action binding key is invalid")
            return key
        key = secrets.token_bytes(32)
        descriptor = os.open(
            key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        try:
            os.write(descriptor, key)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return key


def _digest(root: Path, label: str, value: object) -> str:
    material = json.dumps(
        [label, str(root.resolve()), value], sort_keys=True, separators=(",", ":")
    )
    return hmac.new(binding_key(root), material.encode(), hashlib.sha256).hexdigest()


def _signature(root: Path, grant: ExternalAccessGrant) -> str:
    return _digest(root, "external-access-v1", grant.model_dump(mode="json", exclude={"signature"}))


def external_path(path: Path) -> Path:
    """Reject aliases and traversal before resolving, including missing future targets."""
    if not path.is_absolute() or ".." in path.parts or "\x00" in str(path):
        raise ValueError("external paths must be absolute and cannot contain traversal or NUL")
    if any(part.casefold() in PROTECTED_COMPONENTS for part in path.parts):
        raise ValueError("protected external path cannot be granted")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("external access cannot follow symlinks")
    if path.exists():
        info = path.stat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError("external access requires regular files or directories")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError("external access cannot use multiply linked files")
    return path.resolve()


def has_external_writes(grant: ExternalAccessGrant) -> bool:
    return any(WRITE_OPERATIONS.intersection(rule.operations) for rule in grant.scope.paths)


def _normalize_scope(root: Path, request: ExternalAccessRequest) -> ExternalAccessRequest:
    scope = request.model_copy(deep=True)
    now = utc_now()
    if not now < scope.expires_at <= now + timedelta(hours=24):
        raise ValueError("external access must expire in the future within 24 hours")
    broad = {Path.home().resolve(), Path(root.anchor), root, *root.parents}
    broad.update(Path(p) for p in ("/Users", "/home", "/tmp", "/var", "/etc", "/usr", "/opt"))
    for rule in scope.paths:
        path = external_path(Path(rule.path))
        if path.is_relative_to(root) or path in broad:
            raise ValueError(
                "external scopes must name specific targets, not project, home, or system roots"
            )
        rule.path = str(path)
        if not path.exists() and not path.parent.is_dir():
            raise ValueError("external file targets require an existing parent directory")
        if rule.children:
            if not path.is_dir():
                raise ValueError("a transaction anchor must be an existing directory")
            if PathOperation.LIST in rule.operations:
                raise ValueError("grant directory listing separately from child-file access")
            for name in rule.children:
                if (
                    name in {".", ".."}
                    or any(c in name for c in "/\\?[]\x00")
                    or not name
                    or name.count("*") > 1
                    or ("*" in name and len(name.split("*", 1)[0]) < 4)
                ):
                    raise ValueError(
                        "child names must be exact or use one wildcard after a specific prefix"
                    )
                if any(
                    fnmatch.fnmatchcase(protected, name.casefold())
                    for protected in PROTECTED_COMPONENTS
                ):
                    raise ValueError("child rule includes a protected external path")
        elif path.is_dir() and any(
            op not in {PathOperation.STAT, PathOperation.LIST} for op in rule.operations
        ):
            raise ValueError(
                "directories allow only stat/list; enumerate child names for file access"
            )
        elif PathOperation.LIST in rule.operations and not path.is_dir():
            raise ValueError("list requires an existing directory")
    for use in scope.arguments:
        use.path = str(external_path(Path(use.path)))
    if len({use.path for use in scope.arguments}) != len(scope.arguments):
        raise ValueError("declare each external command path once")
    return scope


def grant_external_access(
    root: Path,
    task_id: str,
    scope: ExternalAccessRequest,
    *,
    approved: bool,
    argv: list[str] | None = None,
) -> ExternalAccessGrant:
    if not approved:
        raise PolicyBlockedError(
            "external access requires explicit approval of scope, expiry, task, and exact command"
        )
    root = root.resolve()
    with task_lock(root, task_id):
        state = load_task(root, task_id)
        if state.status not in {TaskStatus.ACTIVE, TaskStatus.PAUSED, TaskStatus.ESCALATED}:
            raise PolicyBlockedError("external access requires a live task")
        if HarnessPaths(root).stop_file.exists():
            raise PolicyBlockedError("emergency stop is active")
        normalized = _normalize_scope(root, scope)
        if (
            any(WRITE_OPERATIONS.intersection(rule.operations) for rule in normalized.paths)
            and not argv
        ):
            raise ValueError("external writes require an exact reviewed command")
        if argv is not None and (
            not argv or any(not item.strip() or "\x00" in item for item in argv)
        ):
            raise ValueError("external command arguments must be nonblank and contain no NUL")
        grant = ExternalAccessGrant(
            grant_id=f"access-{secrets.token_hex(12)}",
            task_id=task_id,
            scope=normalized,
            approved_at=utc_now(),
            command_hmac=_digest(root, "external-command-v1", argv) if argv else None,
            command_display=redact_argv(argv) if argv else [],
        )
        for use in normalized.arguments:
            if reason := external_path_reason(grant, Path(use.path), use.operation):
                raise ValueError(f"command path declaration is outside scope: {reason}")
        grant.signature = _signature(root, grant)
        state.external_access.append(grant)
        _save_task_unlocked(root, state)
        record_event(
            root,
            "external-access.granted",
            task_id=task_id,
            payload={
                "grant_id": grant.grant_id,
                "scope": normalized.model_dump(mode="json"),
                "argv": grant.command_display,
            },
        )
        return grant


def load_access_grant(root: Path, task_id: str, grant_id: str) -> ExternalAccessGrant:
    state = load_task(root, task_id)
    grant = next((g for g in state.external_access if g.grant_id == grant_id), None)
    if grant is None or grant.task_id != task_id:
        raise PolicyBlockedError("external grant does not belong to this task")
    if not hmac.compare_digest(grant.signature, _signature(root, grant)):
        raise PolicyBlockedError("external grant binding is invalid")
    if grant.revoked_at or utc_now() >= grant.scope.expires_at:
        raise PolicyBlockedError("external grant is revoked or expired")
    if state.status not in {TaskStatus.ACTIVE, TaskStatus.PAUSED, TaskStatus.ESCALATED}:
        raise PolicyBlockedError("external grant requires a live task")
    if HarnessPaths(root).stop_file.exists():
        raise PolicyBlockedError("emergency stop is active")
    return grant


def validate_access_grant(
    root: Path, task_id: str | None, grant: ExternalAccessGrant, argv: list[str] | None = None
) -> None:
    if task_id is None or grant != load_access_grant(root, task_id, grant.grant_id):
        raise PolicyBlockedError("external grant is stale or belongs to another task")
    if argv is not None and (
        not grant.command_hmac
        or not hmac.compare_digest(grant.command_hmac, _digest(root, "external-command-v1", argv))
    ):
        raise PolicyBlockedError("command does not match the exact external access approval")


def revoke_external_access(root: Path, task_id: str, grant_id: str) -> None:
    with task_lock(root, task_id):
        state = load_task(root, task_id)
        grant = next((g for g in state.external_access if g.grant_id == grant_id), None)
        if grant is None:
            raise ValueError("external grant does not belong to this task")
        grant.revoked_at = utc_now()
        grant.signature = _signature(root, grant)
        _save_task_unlocked(root, state)
        record_event(
            root, "external-access.revoked", task_id=task_id, payload={"grant_id": grant_id}
        )


def external_path_reason(grant: ExternalAccessGrant, target: Path, operation: str) -> str | None:
    """None means covered. Checking permission never reads file contents."""
    try:
        path = external_path(target)
        for rule in grant.scope.paths:
            anchor = external_path(Path(rule.path))
            if operation == "transaction":
                if (
                    path == anchor
                    and rule.children
                    and WRITE_OPERATIONS.intersection(rule.operations)
                ):
                    return None
                continue
            if operation not in rule.operations:
                continue
            matches = (
                path == anchor
                if not rule.children
                else path.parent == anchor
                and any(fnmatch.fnmatchcase(path.name, name) for name in rule.children)
            )
            if not matches:
                continue
            if operation in {"read", "create", "replace", "delete"} and path.is_dir():
                return "file operation cannot target a directory"
            if operation == "create" and path.exists():
                return "create permission cannot replace an existing file"
            if operation in {"read", "replace", "delete"} and not path.is_file():
                return "operation requires an existing regular file"
            return None
    except (OSError, ValueError, RuntimeError) as error:
        return str(error)
    return "external path or operation is outside the approved scope"
