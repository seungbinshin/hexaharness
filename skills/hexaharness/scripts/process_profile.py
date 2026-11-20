#!/usr/bin/env -S python3 -I
"""Resolve the HexaHarness process profile for the current host.

Process modules tell the host agent how to plan, repair, verify, and review. They compensate
for limits of the host model, so the operator selects them for each host. Runtime controls do
not depend on the profile.

The profile is an operator decision. This script reads one environment variable and never
inspects the model, the host, or a project. An unset or unrecognized value resolves to every
module, which is the behavior of HexaHarness 1.0.

The script uses only the Python standard library, reads no file, and writes nothing except one
JSON object on standard output.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence

ENVIRONMENT_VARIABLE = "HEXAHARNESS_PROFILE"
MODULES = ("plan", "repair", "verify", "review")
SCHEMA_VERSION = 1
USAGE = f"usage: process_profile.py  (reads {ENVIRONMENT_VARIABLE}; takes no arguments)"


def _result(
    profile: str, modules: Sequence[str], source: str, warning: str | None
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "modules": list(modules),
        # Work outside the runtime is offered only when no process module applies.
        "direct_engagement": not modules,
        "source": source,
        "warning": warning,
    }


def resolve(environment: Mapping[str, str]) -> dict[str, object]:
    """Return the process profile that the operator selected for this host."""
    value = environment.get(ENVIRONMENT_VARIABLE, "").strip().lower()
    if not value:
        return _result("guided", MODULES, "default", None)
    if value == "guided":
        return _result("guided", MODULES, "environment", None)
    if value == "lean":
        return _result("lean", (), "environment", None)

    requested = [part.strip() for part in value.split(",")]
    recognized = all(part in MODULES for part in requested)
    if recognized and len(set(requested)) == len(requested):
        selected = tuple(module for module in MODULES if module in requested)
        profile = "guided" if selected == MODULES else "custom"
        return _result(profile, selected, "environment", None)

    # Fail toward more guidance. The value is not echoed: it is operator input of unknown content.
    return _result(
        "guided",
        MODULES,
        "invalid-environment",
        f"{ENVIRONMENT_VARIABLE} is not 'guided', 'lean', or a comma-separated list of "
        f"{', '.join(MODULES)}; every module applies",
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        print(USAGE, file=sys.stderr)
        return 2
    print(json.dumps(resolve(os.environ), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
