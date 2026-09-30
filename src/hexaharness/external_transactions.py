"""Metadata-only contracts for reviewed, anchored external executors.

This module never performs an external mutation or claims to hold the executor's lock.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from hexaharness.models import (
    ExternalAccessRequest,
    ExternalHardlinkPair,
    FileIdentity,
    PathOperation,
)

FILE_OPS = {"stat", "read", "create", "replace", "delete"}
DIRECTORY_OPS = {"stat", "list", "mkdir", "rmdir"}
MUTATIONS = {"create", "replace", "delete", "mkdir", "rmdir", "link"}


def identity(path: Path) -> FileIdentity:
    info = path.lstat()
    return FileIdentity(
        device=info.st_dev, inode=info.st_ino, owner=info.st_uid, mode=stat.S_IMODE(info.st_mode)
    )


def _name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) or name in {".", ".."}:
        raise ValueError("transaction-name: use an exact single-component name, no wildcard")


def _private(path: Path, parent: Path, *, directory: bool = False) -> None:
    from hexaharness.external_access import external_path

    # The caller validates pair link topology separately; directories never need an exception.
    if path.is_symlink():
        raise ValueError("transaction-type: symlink rejected")
    for ancestor in path.parents:
        external_path(ancestor)
    info = path.lstat()
    required = stat.S_ISDIR if directory else stat.S_ISREG
    if not required(info.st_mode):
        raise ValueError("transaction-type: unexpected file type")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise ValueError("transaction-permissions: owner or owner-only mode mismatch")
    if info.st_dev != parent.stat().st_dev:
        raise ValueError("transaction-device: mount boundary rejected")


def pair_state(parent: Path, pair: ExternalHardlinkPair) -> str:
    """Recognize absent, source-only, destination-only, or the exact two-name pair."""
    paths = [parent / pair.source.name, parent / pair.destination.name]
    infos: list[os.stat_result | None] = []
    for path in paths:
        if path.exists() or path.is_symlink():
            _private(path, parent)
            infos.append(path.lstat())
        else:
            infos.append(None)
    first, second = infos
    if first is not None and second is not None:
        if (
            (first.st_dev, first.st_ino) != (second.st_dev, second.st_ino)
            or first.st_nlink != 2
            or second.st_nlink != 2
        ):
            raise ValueError(
                "hardlink-topology: two declared names must be the same inode with nlink=2"
            )
        return "paired"
    remaining = first if first is not None else second
    if remaining is not None and remaining.st_nlink != 1:
        raise ValueError("hardlink-topology: single declared name has an unknown additional link")
    return "source-only" if first else "destination-only" if second else "absent"


def validate_hardlink_path(scope: ExternalAccessRequest, path: Path) -> bool:
    for transaction in scope.transactions:
        parent = Path(transaction.parent)
        for pair in transaction.hardlink_pairs:
            if path in {parent / pair.source.name, parent / pair.destination.name}:
                pair_state(parent, pair)
                return True
    return False


def transaction_targets(scope: ExternalAccessRequest) -> list[tuple[Path, list[PathOperation]]]:
    targets = []
    for transaction in scope.transactions:
        parent = Path(transaction.parent)
        for scratch in transaction.scratch_directories:
            directory = parent / scratch.name
            targets.append((directory, scratch.operations))
            targets.extend((directory / child.name, child.operations) for child in scratch.files)
        for pair in transaction.hardlink_pairs:
            targets.extend(
                (parent / member.name, member.operations)
                for member in (pair.source, pair.destination)
            )
    return targets


def transaction_writes(scope: ExternalAccessRequest) -> bool:
    return any(MUTATIONS.intersection(ops) for _, ops in transaction_targets(scope))


def normalize_transactions(root: Path, scope: ExternalAccessRequest) -> dict[str, FileIdentity]:
    from hexaharness.external_access import external_path

    bindings: dict[str, FileIdentity] = {}
    if scope.transactions and os.name != "posix":
        raise ValueError("transaction-platform: anchored-v1 requires POSIX ownership metadata")
    parents: set[Path] = set()
    for transaction in scope.transactions:
        parent = external_path(Path(transaction.parent))
        broad = {root, *root.parents, Path.home().resolve()}
        broad.update(Path(p) for p in ("/Users", "/home", "/tmp", "/var", "/etc", "/usr", "/opt"))
        if parent in broad or parent.is_relative_to(root) or not parent.is_dir():
            raise ValueError("transaction-parent: require one specific existing external directory")
        if any(
            parent == p or parent.is_relative_to(p) or p.is_relative_to(parent) for p in parents
        ):
            raise ValueError("transaction-parent: overlapping transaction parents")
        parents.add(parent)
        transaction.parent = str(parent)
        _private(parent, parent, directory=True)
        _name(transaction.lock)
        lock = external_path(parent / transaction.lock)
        _private(lock, parent)
        if not any(
            Path(rule.path) == lock
            and not rule.children
            and PathOperation.READ in rule.operations
            and not MUTATIONS.intersection(rule.operations)
            for rule in scope.paths
        ):
            raise ValueError(
                "transaction-lock: declare the existing common lock as an exact read path"
            )
        bindings[str(parent)] = identity(parent)
        bindings[str(lock)] = identity(lock)
        names = {transaction.lock}
        if not transaction.scratch_directories and not transaction.hardlink_pairs:
            raise ValueError("transaction-empty: declare a scratch directory or hardlink pair")
        for scratch in transaction.scratch_directories:
            _name(scratch.name)
            if scratch.name in names or not set(scratch.operations) <= DIRECTORY_OPS:
                raise ValueError(
                    "scratch-operation: duplicate name or unsupported directory operation"
                )
            names.add(scratch.name)
            directory = external_path(parent / scratch.name)
            child_names: set[str] = set()
            for child in scratch.files:
                _name(child.name)
                if child.name in child_names or not set(child.operations) <= FILE_OPS:
                    raise ValueError(
                        "scratch-operation: duplicate child or unsupported file operation"
                    )
                child_names.add(child.name)
                external_path(directory / child.name)
            if directory.exists():
                _private(directory, parent, directory=True)
                if PathOperation.MKDIR in scratch.operations:
                    raise ValueError(
                        "scratch-collision: fresh directory already exists; reconcile first"
                    )
                if MUTATIONS.intersection(scratch.operations) or any(
                    MUTATIONS.intersection(child.operations) for child in scratch.files
                ):
                    if (
                        scratch.created_identity != identity(directory)
                        or not scratch.recovery_evidence
                    ):
                        raise ValueError(
                            "scratch-provenance: recovery needs creation identity and evidence"
                        )
                    evidence = root / scratch.recovery_evidence
                    if (
                        evidence.is_symlink()
                        or not evidence.resolve().is_relative_to(root)
                        or not evidence.is_file()
                    ):
                        raise ValueError(
                            "scratch-provenance: require a reviewed project-local creation receipt"
                        )
                bindings[str(directory)] = identity(directory)
            elif scratch.created_identity is not None or scratch.recovery_evidence is not None:
                raise ValueError("scratch-provenance: recovery target is missing")
            elif PathOperation.MKDIR not in scratch.operations:
                raise ValueError("scratch-missing: new directory requires mkdir permission")
        for pair in transaction.hardlink_pairs:
            for member in (pair.source, pair.destination):
                _name(member.name)
                if member.name in names:
                    raise ValueError("hardlink-name: pair names must be distinct and exclusive")
                names.add(member.name)
                allowed = {"stat", "read", "create", "delete"}
                if member is pair.destination:
                    allowed.add("link")
                if not set(member.operations) <= allowed:
                    raise ValueError(
                        "hardlink-operation: replacement and source linking are unsupported"
                    )
                external_path(parent / member.name, scope=scope)
            if (
                PathOperation.LINK in pair.destination.operations
                and PathOperation.READ not in pair.source.operations
            ):
                raise ValueError("hardlink-operation: linking requires source read permission")
            pair_state(parent, pair)
        # A legacy rule must not provide an alternate route into the new contract or lock writes.
        for rule in scope.paths:
            target = Path(rule.path)
            for name in names:
                reserved = parent / name
                if rule.children and target == parent:
                    import fnmatch

                    if any(fnmatch.fnmatchcase(name, pattern) for pattern in rule.children):
                        raise ValueError(
                            "transaction-overlap: legacy child rule overlaps transaction names"
                        )
                elif target == reserved or target.is_relative_to(reserved):
                    if target == lock and not MUTATIONS.intersection(rule.operations):
                        continue
                    raise ValueError("transaction-overlap: legacy rule overlaps transaction target")
    validate_transactions(scope, bindings)
    return bindings


def validate_transactions(scope: ExternalAccessRequest, bindings: dict[str, FileIdentity]) -> None:
    from hexaharness.external_access import external_path

    if scope.transactions and os.name != "posix":
        raise ValueError("transaction-platform: anchored-v1 requires POSIX ownership metadata")
    for transaction in scope.transactions:
        parent = external_path(Path(transaction.parent))
        for path in (parent, parent / transaction.lock):
            external_path(path)
            _private(path, parent, directory=path == parent)
            if identity(path) != bindings.get(str(path)):
                raise ValueError("transaction-identity: parent or lock changed")
        for scratch in transaction.scratch_directories:
            directory = external_path(parent / scratch.name)
            saved = bindings.get(str(directory))
            if directory.exists():
                _private(directory, parent, directory=True)
                if saved is None or identity(directory) != saved:
                    raise ValueError(
                        "scratch-identity: unbound directory; use a separate inspection grant"
                    )
                expected = {child.name for child in scratch.files}
                if any(child.name not in expected for child in directory.iterdir()):
                    raise ValueError("scratch-contents: unexpected entry; preserve and reconcile")
                for child in directory.iterdir():
                    external_path(child)
                    _private(child, parent)
            elif saved is not None:
                raise ValueError("scratch-identity: bound directory disappeared; reconcile")
        for pair in transaction.hardlink_pairs:
            pair_state(parent, pair)


def transaction_path_reason(
    scope: ExternalAccessRequest, bindings: dict[str, FileIdentity], path: Path, operation: str
) -> tuple[bool, str | None]:
    for transaction in scope.transactions:
        parent = Path(transaction.parent)
        if path != parent and not path.is_relative_to(parent):
            continue
        validate_transactions(scope, bindings)
        if path == parent and operation == "transaction":
            return True, None
        for scratch in transaction.scratch_directories:
            directory = parent / scratch.name
            if path == directory:
                if operation not in scratch.operations:
                    return True, "scratch-operation: operation was not approved"
                if operation == "mkdir" and directory.exists():
                    return True, "scratch-collision: directory already exists"
                if operation in {"list", "rmdir"} and not directory.is_dir():
                    return True, "scratch-missing: operation requires an existing directory"
                if operation == "rmdir" and any(directory.iterdir()):
                    return True, "scratch-not-empty: recursive cleanup is forbidden"
                return True, None
            if path.is_relative_to(directory):
                for child in scratch.files:
                    if path == directory / child.name:
                        return True, _file_reason(path, operation, child.operations)
                return True, "scratch-name: undeclared child or depth greater than one"
        for pair in transaction.hardlink_pairs:
            for member in (pair.source, pair.destination):
                if path != parent / member.name:
                    continue
                if operation == "link" and operation in member.operations:
                    if path.exists() or pair_state(parent, pair) != "source-only":
                        return (
                            True,
                            "hardlink-publish: require source-only state and absent destination",
                        )
                    return True, None
                return True, _file_reason(path, operation, member.operations)
    return False, None


def _file_reason(path: Path, operation: str, allowed: list[PathOperation]) -> str | None:
    if operation not in allowed:
        return "transaction-operation: operation was not approved"
    if operation == "create" and path.exists():
        return "transaction-collision: create cannot overwrite an existing name"
    if operation in {"read", "replace", "delete"} and not path.is_file():
        return "transaction-missing: operation requires an existing regular file"
    return None
