"""Private Windows command supervisor; a lifetime job, not an OS security sandbox.

The only job handle belongs to this process. The command starts after assignment,
so ordinary CreateProcess descendants inherit containment without a launch race.
Normal exit and forced termination both close the handle and terminate descendants.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
from ctypes import wintypes


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", ctypes.c_ulonglong * 6),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def main() -> None:
    if os.name != "nt" or len(sys.argv) < 2:
        raise SystemExit(126)
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise SystemExit(126)
    kernel = loader("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not kernel.SetInformationJobObject(
        job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
    ):
        print("HexaHarness could not establish Windows process containment", file=sys.stderr)
        if job:
            kernel.CloseHandle(job)
        raise SystemExit(126)
    if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        print("HexaHarness host denied Windows process containment", file=sys.stderr)
        kernel.CloseHandle(job)
        raise SystemExit(126)
    # Keep the supervisor alive until its child exits or the outer runtime kills it.
    signal.signal(getattr(signal, "SIGBREAK", signal.SIGTERM), signal.SIG_IGN)
    try:
        code = subprocess.call(sys.argv[1:])
    except FileNotFoundError:
        print("command not found", file=sys.stderr)
        code = 127
    except OSError:
        print("command could not be launched", file=sys.stderr)
        code = 126
    sys.stdout.flush()
    sys.stderr.flush()
    # Closing our job before exit would kill us and lose the real command status.
    # Process teardown closes the non-inheritable handle after preserving that status.
    # Popen exposes unsigned Windows exception statuses; os._exit accepts a C int.
    os._exit(ctypes.c_int32(code).value)


if __name__ == "__main__":
    main()
