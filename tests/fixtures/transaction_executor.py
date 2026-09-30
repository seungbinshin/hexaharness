"""Synthetic Linux executor for conformance tests, not a production issuer or SDK.

The grant authorizes names; this reviewed executor owns fd anchoring, locking, creation
provenance, no-replace/exchange primitives, fsync, and logical recovery. No live inputs.
"""

import ctypes
import fcntl
import json
import os
import stat
import sys
from pathlib import Path

parent = Path(sys.argv[1])
failure = sys.argv[2]
receipt = {"phase": "start", "created": {}}
directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
parent_info = os.fstat(directory_fd)
lock_fd = os.open("common.lock", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
lock_info = os.fstat(lock_fd)
fcntl.flock(lock_fd, fcntl.LOCK_EX)


def ident(info):
    return (info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode))


def persist_receipt():
    receipt["parent_identity"] = list(ident(parent_info))
    receipt["lock_identity"] = list(ident(lock_info))
    with Path("receipt.next").open("w") as stream:
        json.dump(receipt, stream)
        stream.flush()
        os.fsync(stream.fileno())
    Path("receipt.next").replace("receipt.json")
    evidence_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(evidence_fd)
    finally:
        os.close(evidence_fd)


def check_anchor():
    assert ident(parent.lstat()) == ident(parent_info)
    assert ident(os.stat("common.lock", dir_fd=directory_fd, follow_symlinks=False)) == ident(
        lock_info
    )
    assert parent_info.st_uid == lock_info.st_uid == os.geteuid()
    assert stat.S_IMODE(parent_info.st_mode) == 0o700
    assert stat.S_IMODE(lock_info.st_mode) == 0o600


def info_at(fd, name):
    return os.stat(name, dir_fd=fd, follow_symlinks=False)


def create(fd, name, data):
    check_anchor()
    handle = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        info = os.fstat(handle)
        assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        assert info.st_dev == parent_info.st_dev and info.st_uid == os.geteuid()
        os.write(handle, data.encode())
        os.fsync(handle)
        receipt["created"][name] = list(ident(info))
        persist_receipt()
        return ident(info)
    finally:
        os.close(handle)


def rename(fd, source, destination, flag):
    check_anchor()
    library = ctypes.CDLL(None, use_errno=True)
    primitive = library.renameat2
    primitive.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    primitive.restype = ctypes.c_int
    if primitive(fd, source.encode(), fd, destination.encode(), flag) != 0:
        raise OSError(ctypes.get_errno(), "atomic primitive failed")


def probe():
    name = ".probe-op1.scratch"
    check_anchor()
    os.mkdir(name, 0o700, dir_fd=directory_fd)
    child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
    child_id = ident(os.fstat(child_fd))
    receipt["scratch_identity"] = dict(
        zip(("device", "inode", "owner", "mode"), child_id, strict=True)
    )
    persist_receipt()
    owned = set()
    try:
        assert child_id == ident(info_at(directory_fd, name))
        assert child_id[0] == parent_info.st_dev and child_id[2:] == (os.geteuid(), 0o700)
        owned.add(create(child_fd, "first", "first"))
        owned.add(create(child_fd, "second", "second"))
        if failure == "unsupported":
            raise OSError("injected unsupported primitive")
        rename(child_fd, "first", "moved", 1)  # RENAME_NOREPLACE
        rename(child_fd, "moved", "second", 2)  # RENAME_EXCHANGE
        if failure == "foreign":
            create(child_fd, "foreign", "injected unrelated entry")
        if failure == "probe":
            raise RuntimeError("injected interruption; retain creation receipt")
    finally:
        try:
            if failure != "probe":
                check_anchor()
                assert ident(info_at(directory_fd, name)) == child_id
                entries = os.listdir(child_fd)  # noqa: PTH208 - enumerate the retained directory fd
                assert set(entries) <= {"first", "second", "moved"}, "unexpected child; preserve"
                assert all(ident(info_at(child_fd, entry)) in owned for entry in entries)
                for entry in entries:
                    os.unlink(entry, dir_fd=child_fd)
                os.fsync(child_fd)
                assert not os.listdir(child_fd)  # noqa: PTH208 - verify the same directory fd
                assert ident(info_at(directory_fd, name)) == child_id
                os.rmdir(name, dir_fd=directory_fd)
                os.fsync(directory_fd)
        finally:
            os.close(child_fd)


def publish(kind, content):
    source, destination = f"{kind}-op1.stage", f"{kind}.json"
    created = create(directory_fd, source, json.dumps(content))
    check_anchor()
    assert ident(info_at(directory_fd, source)) == created
    os.link(
        source, destination, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False
    )
    assert ident(info_at(directory_fd, destination)) == created
    assert (
        info_at(directory_fd, source).st_nlink == info_at(directory_fd, destination).st_nlink == 2
    )
    os.fsync(directory_fd)
    receipt["phase"] = kind
    persist_receipt()
    if failure == kind + "-crash":
        os._exit(77)
    if failure == kind:
        raise RuntimeError("injected interruption after link")


def read_pair(kind):
    source, destination = f"{kind}-op1.stage", f"{kind}.json"
    first, second = info_at(directory_fd, source), info_at(directory_fd, destination)
    assert ident(first) == ident(second) and first.st_nlink == second.st_nlink == 2
    assert first.st_uid == os.geteuid() and stat.S_IMODE(first.st_mode) == 0o600
    handle = os.open(destination, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    with os.fdopen(handle) as stream:
        assert ident(os.fstat(stream.fileno())) == ident(second)
        return json.load(stream)


def finish():
    journal, profile = read_pair("journal"), read_pair("profile")
    assert journal["operation"] == profile["id"] == "op1"
    assert journal["profile"] == "profile.json"
    assert profile["token"] == "SYNTHETIC_ENROLLMENT_SECRET"
    # This synthetic format has one deterministic pending state; real issuers need their own
    # journal stage/digest/registration checks and compatibility with historical formats.
    create(directory_fd, "registry-next.json", json.dumps({"pending": profile["id"]}))
    os.replace(
        "registry-next.json", "registry.json", src_dir_fd=directory_fd, dst_dir_fd=directory_fd
    )
    os.fsync(directory_fd)
    receipt["phase"] = "published"
    if failure == "published":
        raise RuntimeError("injected exception after publication")
    for kind in ("journal", "profile"):
        read_pair(kind)
        os.unlink(f"{kind}-op1.stage", dir_fd=directory_fd)
    os.fsync(directory_fd)
    receipt["phase"] = "verified"
    receipt["pending_matches_profile"] = True


try:
    check_anchor()
    print("SYNTHETIC_ENROLLMENT_SECRET")
    print("SYNTHETIC_ENROLLMENT_SECRET", file=sys.stderr)
    if failure == "recover":
        finish()
    else:
        probe()
        publish("journal", {"operation": "op1", "profile": "profile.json"})
        publish("profile", {"id": "op1", "token": "SYNTHETIC_ENROLLMENT_SECRET"})
        finish()
finally:
    persist_receipt()
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
    os.close(directory_fd)
