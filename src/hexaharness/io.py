from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SECRET_FLAG_NAMES = {
    "--access-key-id",
    "--access-token",
    "--api-key",
    "--auth-token",
    "--authorization",
    "--client-secret",
    "--cookie",
    "--credential",
    "--credentials",
    "--github-token",
    "--oauth-token",
    "--oauth2-bearer",
    "--passphrase",
    "--password",
    "--passwd",
    "--private-key",
    "--proxy-user",
    "--secret",
    "--token",
    "--user",
}
HEADER_FLAG_NAMES = {"--header", "--proxy-header"}
SHORT_SECRET_FLAGS_BY_COMMAND = {
    "curl": {"-b", "-u"},
}
ATTACHED_ONLY_SECRET_FLAGS_BY_COMMAND = {
    "mariadb": {"-p"},
    "mariadb-admin": {"-p"},
    "mariadb-dump": {"-p"},
    "mysql": {"-p"},
    "mysqladmin": {"-p"},
    "mysqlcheck": {"-p"},
    "mysqldump": {"-p"},
}
SENSITIVE_NAME_PARTS = {
    "access-key",
    "access-key-id",
    "access-token",
    "api-key",
    "auth-token",
    "authorization",
    "client-secret",
    "cookie",
    "credential",
    "credentials",
    "oauth-token",
    "passphrase",
    "password",
    "passwd",
    "private-key",
    "proxy-authorization",
    "secret",
    "token",
}
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(?P<name>[a-z0-9_.-]*"
    r"(?:access[_-]?key|access[_-]?token|api[_-]?key|auth[_-]?token|authorization|"
    r"client[_-]?secret|cookie|credential|oauth[_-]?token|passphrase|password|passwd|"
    r"private[_-]?key|secret|token)"
    r"[a-z0-9_.-]*)=(?P<value>[^\s;&]+)"
)
URL_USERINFO = re.compile(r"(?i)(?P<prefix>[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/@\s]+)@")


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, line.encode("utf-8"))
    finally:
        os.close(descriptor)


def slugify(value: str, *, fallback: str = "task", limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or fallback)[:limit].rstrip("-")


def _normalized_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized


def _is_sensitive_name(value: str) -> bool:
    normalized = _normalized_name(value)
    if not normalized:
        return False
    return any(
        normalized == part or normalized.endswith(f"-{part}") for part in SENSITIVE_NAME_PARTS
    )


def _is_header_flag(value: str) -> bool:
    return value == "-H" or value.casefold() in HEADER_FLAG_NAMES


def _command_name(value: str) -> str:
    name = value.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    for suffix in (".exe", ".cmd", ".bat"):
        name = name.removesuffix(suffix)
    return name


def _env_nested_command_index(argv: list[str], wrapper_index: int) -> int | None:
    index = wrapper_index + 1
    options_with_values = {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}
    while index < len(argv):
        argument = argv[index]
        if argument in {"--help", "--version"}:
            return None
        if argument == "--":
            index += 1
            break
        if argument in options_with_values:
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        if argument.startswith(("-C", "--chdir=", "-S", "--split-string=", "--unset=")):
            index += 1
            continue
        if argument.startswith("-") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argument):
            index += 1
            continue
        break
    return index if index < len(argv) else None


def _timeout_nested_command_index(argv: list[str], wrapper_index: int) -> int | None:
    index = wrapper_index + 1
    options_with_values = {"-k", "--kill-after", "-s", "--signal", "-t"}
    while index < len(argv):
        argument = argv[index]
        if argument in {"--help", "--version"}:
            return None
        if argument == "--":
            index += 1
            break
        if argument in options_with_values:
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        if argument.startswith(("--kill-after=", "--signal=")) or re.fullmatch(
            r"-(?:k|s|t).+", argument
        ):
            index += 1
            continue
        if argument.startswith("-"):
            index += 1
            continue
        break
    # The first positional operand is the duration, followed by the command.
    return index + 1 if index + 1 < len(argv) else None


def _nice_nested_command_index(argv: list[str], wrapper_index: int) -> int | None:
    index = wrapper_index + 1
    while index < len(argv):
        argument = argv[index]
        if argument in {"--help", "--version"}:
            return None
        if argument == "--":
            index += 1
            break
        if argument in {"-n", "--adjustment"}:
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        if argument.startswith(("-n", "--adjustment=")) or re.fullmatch(r"-\d+", argument):
            index += 1
            continue
        if argument.startswith("-"):
            return None
        break
    return index if index < len(argv) else None


def _simple_nested_command_index(
    argv: list[str],
    wrapper_index: int,
    *,
    boolean_options: set[str],
    value_options: set[str],
    attached_value_prefixes: tuple[str, ...],
) -> int | None:
    """Parse wrappers whose options precede a direct command without positional metadata."""
    index = wrapper_index + 1
    while index < len(argv):
        argument = argv[index]
        if argument in {"--help", "--version", "-h", "-V"}:
            return None
        if argument == "--":
            index += 1
            break
        if argument in value_options:
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        if argument in boolean_options or argument.startswith(attached_value_prefixes):
            index += 1
            continue
        if argument.startswith("-"):
            return None
        break
    return index if index < len(argv) else None


def _ionice_nested_command_index(argv: list[str], wrapper_index: int) -> int | None:
    index = wrapper_index + 1
    while index < len(argv):
        argument = argv[index]
        if argument in {"--help", "--version", "-h", "-V"}:
            return None
        if argument == "--":
            return index + 1 if index + 1 < len(argv) else None
        # PID-selection modes mutate an existing process and do not wrap a command.
        if argument in {"-p", "--pid", "-P", "--pgid", "-u", "--uid"} or argument.startswith(
            ("-p", "-P", "-u", "--pid=", "--pgid=", "--uid=")
        ):
            return None
        if argument in {"-c", "--class", "-n", "--classdata"}:
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        if argument in {"-t", "--ignore"} or argument.startswith(
            ("-c", "-n", "--class=", "--classdata=")
        ):
            index += 1
            continue
        if argument.startswith("-"):
            return None
        return index
    return None


def _taskset_nested_command_index(argv: list[str], wrapper_index: int) -> int | None:
    index = wrapper_index + 1
    while index < len(argv):
        argument = argv[index]
        if argument in {"--help", "--version", "-h", "-V"}:
            return None
        if argument == "--":
            index += 1
            break
        # `taskset -p` operates on an existing PID rather than launching a command.
        if argument in {"-p", "--pid"} or argument.startswith("--pid="):
            return None
        if argument in {"-a", "--all-tasks", "-c", "--cpu-list"}:
            index += 1
            continue
        if argument.startswith("-"):
            return None
        break
    # The first positional operand is a CPU mask/list, followed by the command.
    return index + 1 if index + 1 < len(argv) else None


def _strace_nested_command_index(argv: list[str], wrapper_index: int) -> int | None:
    boolean_options = {
        "-c",
        "-C",
        "-f",
        "-ff",
        "-k",
        "-q",
        "-qq",
        "-r",
        "-t",
        "-tt",
        "-ttt",
        "-T",
        "-v",
        "-w",
        "-x",
        "-xx",
        "-y",
        "-yy",
        "-z",
        "-Z",
    }
    value_options = {"-b", "-e", "-E", "-I", "-o", "-s", "-u"}
    attached_value_prefixes = (
        "-b",
        "-e",
        "-E",
        "-I",
        "-o",
        "-s",
        "-u",
        "--detach-on=",
        "--env=",
        "--output=",
        "--string-limit=",
        "--trace=",
        "--user=",
    )
    index = wrapper_index + 1
    while index < len(argv):
        argument = argv[index]
        if argument in {"--help", "--version", "-h", "-V"}:
            return None
        if argument == "--":
            return index + 1 if index + 1 < len(argv) else None
        # Attach mode traces an existing process and has no nested command.
        if argument in {"-p", "--attach"} or argument.startswith(("-p", "--attach=")):
            return None
        if argument in value_options:
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        if argument in boolean_options or argument.startswith(attached_value_prefixes):
            index += 1
            continue
        if argument.startswith("-"):
            return None
        return index
    return None


def _uv_nested_command_index(argv: list[str], wrapper_index: int) -> int | None:
    if wrapper_index + 1 >= len(argv) or argv[wrapper_index + 1].casefold() != "run":
        return None
    index = wrapper_index + 2
    value_options = {
        "--config-file",
        "--directory",
        "--env-file",
        "--extra-index-url",
        "--find-links",
        "--index",
        "--index-url",
        "--package",
        "--project",
        "--python",
        "--resolution",
        "--with",
        "--with-editable",
        "--with-requirements",
    }
    boolean_options = {
        "--active",
        "--all-extras",
        "--all-groups",
        "--exact",
        "--frozen",
        "--isolated",
        "--locked",
        "--no-dev",
        "--no-project",
        "--no-sync",
        "--offline",
        "--quiet",
        "-q",
        "--verbose",
        "-v",
    }
    while index < len(argv):
        argument = argv[index]
        if argument in {"--help", "-h"}:
            return None
        if argument == "--":
            return index + 1 if index + 1 < len(argv) else None
        if argument in {"-m", "--module", "-s", "--script"}:
            return index + 1 if index + 1 < len(argv) else None
        if argument in value_options:
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        if argument in boolean_options or (argument.startswith("--") and "=" in argument):
            index += 1
            continue
        if argument.startswith("-"):
            return None
        return index
    return None


def _npx_nested_command_index(argv: list[str], wrapper_index: int) -> int | None:
    return _simple_nested_command_index(
        argv,
        wrapper_index,
        boolean_options={"--ignore-existing", "--no", "--quiet", "--yes", "-y"},
        value_options={
            "--cache",
            "--call",
            "-c",
            "--node-options",
            "--package",
            "-p",
            "--registry",
            "--shell",
            "--userconfig",
        },
        attached_value_prefixes=(
            "-c",
            "-p",
            "--cache=",
            "--call=",
            "--node-options=",
            "--package=",
            "--registry=",
            "--shell=",
            "--userconfig=",
        ),
    )


def _python_module_index(argv: list[str], wrapper_index: int) -> int | None:
    index = wrapper_index + 1
    boolean_options = {
        "-B",
        "-d",
        "-E",
        "-I",
        "-i",
        "-O",
        "-OO",
        "-P",
        "-q",
        "-s",
        "-S",
        "-u",
        "-v",
        "-x",
    }
    while index < len(argv):
        argument = argv[index]
        if argument == "-m":
            return index + 1 if index + 1 < len(argv) else None
        if argument in {"-c", "--help", "-h", "--version", "-V"}:
            return None
        if argument in {"-W", "-X", "--check-hash-based-pycs"}:
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        if argument in boolean_options or argument.startswith(("-W", "-X")):
            index += 1
            continue
        return None
    return None


def _nested_command_index(argv: list[str], wrapper_index: int) -> int | None:
    command = _command_name(argv[wrapper_index])
    if command == "env":
        return _env_nested_command_index(argv, wrapper_index)
    if command == "timeout":
        return _timeout_nested_command_index(argv, wrapper_index)
    if command == "nice":
        return _nice_nested_command_index(argv, wrapper_index)
    if command in {"busybox", "toybox"}:
        nested = wrapper_index + 1
        return nested if nested < len(argv) and not argv[nested].startswith("-") else None
    if command == "nohup":
        return _simple_nested_command_index(
            argv,
            wrapper_index,
            boolean_options=set(),
            value_options=set(),
            attached_value_prefixes=(),
        )
    if command == "stdbuf":
        return _simple_nested_command_index(
            argv,
            wrapper_index,
            boolean_options=set(),
            value_options={"-e", "--error", "-i", "--input", "-o", "--output"},
            attached_value_prefixes=("-e", "-i", "-o", "--error=", "--input=", "--output="),
        )
    if command == "setsid":
        return _simple_nested_command_index(
            argv,
            wrapper_index,
            boolean_options={"-c", "--ctty", "-f", "--fork", "-w", "--wait"},
            value_options=set(),
            attached_value_prefixes=(),
        )
    if command == "ionice":
        return _ionice_nested_command_index(argv, wrapper_index)
    if command == "taskset":
        return _taskset_nested_command_index(argv, wrapper_index)
    if command == "strace":
        return _strace_nested_command_index(argv, wrapper_index)
    if command == "uv":
        return _uv_nested_command_index(argv, wrapper_index)
    if command == "npx":
        return _npx_nested_command_index(argv, wrapper_index)
    if command == "py" or re.fullmatch(r"(?:python|pypy)(?:\d+(?:\.\d+)*)?", command):
        return _python_module_index(argv, wrapper_index)
    return None


def _effective_command_index(argv: list[str]) -> int | None:
    """Locate the command through transparent wrappers without interpreting shell syntax."""
    if not argv:
        return None
    index = 0
    while (nested := _nested_command_index(argv, index)) is not None:
        if nested <= index or nested >= len(argv):
            break
        index = nested
    return index


def _short_secret_flag_mode(
    argv: list[str], *, command_index: int | None, argument_index: int, flag: str
) -> str | None:
    """Classify ambiguous short options only in known credential-bearing command contexts."""
    if command_index is None or argument_index <= command_index:
        return None
    command = _command_name(argv[command_index])
    if flag in SHORT_SECRET_FLAGS_BY_COMMAND.get(command, set()):
        return "separate-or-attached"
    if flag in ATTACHED_ONLY_SECRET_FLAGS_BY_COMMAND.get(command, set()):
        return "attached-only"
    if (
        command in {"docker", "podman"}
        and argument_index > command_index + 1
        and argv[command_index + 1].casefold() == "login"
        and flag == "-p"
    ):
        return "separate-or-attached"
    return None


def _add_secret(secrets: set[str], value: str) -> None:
    if not value:
        return
    secrets.add(value)
    unquoted = value.strip("'\"")
    if unquoted:
        secrets.add(unquoted)
    scheme, space, credential = unquoted.partition(" ")
    if space and scheme.casefold() in {"basic", "bearer", "token"}:
        secrets.add(credential.strip())
    for separator in ("=", ":"):
        _, present, suffix = unquoted.partition(separator)
        if present and suffix:
            secrets.add(suffix)


def _redact_embedded_assignments(value: str) -> tuple[str, set[str]]:
    secrets: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        _add_secret(secrets, match.group("value"))
        return f"{match.group('name')}=<redacted>"

    return SENSITIVE_ASSIGNMENT.sub(replace, value), secrets


def _redact_header(value: str) -> tuple[str, set[str]]:
    """Redact credential-bearing HTTP headers while preserving the header name."""
    name, separator, header_value = value.partition(":")
    if not separator or not _is_sensitive_name(name):
        return value, set()

    stripped_value = header_value.strip()
    secrets: set[str] = set()
    _add_secret(secrets, stripped_value)
    normalized_name = _normalized_name(name)
    if normalized_name.endswith("authorization"):
        _, space, credential = stripped_value.partition(" ")
        if space:
            _add_secret(secrets, credential.strip())
    if normalized_name.endswith("cookie"):
        for item in stripped_value.split(";"):
            _, equals, cookie_value = item.partition("=")
            if equals:
                _add_secret(secrets, cookie_value.strip())
    return f"{name}: <redacted>", secrets


def _redact_url_userinfo(value: str) -> tuple[str, set[str]]:
    """Remove credentials embedded in URL authority components."""
    secrets: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        _add_secret(secrets, match.group("userinfo"))
        return f"{match.group('prefix')}<redacted>@"

    return URL_USERINFO.sub(replace, value), secrets


def _collect_json_secret_values(value: object, secrets: set[str]) -> None:
    if isinstance(value, dict):
        for nested in value.values():
            _collect_json_secret_values(nested, secrets)
        return
    if isinstance(value, list):
        for nested in value:
            _collect_json_secret_values(nested, secrets)
        return
    if value is None:
        return
    if isinstance(value, str):
        _add_secret(secrets, value)
        return
    _add_secret(secrets, json.dumps(value, ensure_ascii=False))


def _redact_json_value(value: object, secrets: set[str]) -> tuple[object, bool]:
    changed = False
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, nested in value.items():
            if _is_sensitive_name(str(key)):
                _collect_json_secret_values(nested, secrets)
                redacted[str(key)] = "<redacted>"
                changed = True
            else:
                replacement, nested_changed = _redact_json_value(nested, secrets)
                redacted[str(key)] = replacement
                changed = changed or nested_changed
        return redacted, changed
    if isinstance(value, list):
        redacted_items: list[object] = []
        for nested in value:
            replacement, nested_changed = _redact_json_value(nested, secrets)
            redacted_items.append(replacement)
            changed = changed or nested_changed
        return redacted_items, changed
    return value, False


def _redact_json_document(value: str) -> tuple[str, set[str]]:
    """Redact sensitive keys in a complete JSON object or array without invalidating JSON."""
    stripped = value.strip()
    if not stripped.startswith(("{", "[")):
        return value, set()
    try:
        document: object = json.loads(value)
    except json.JSONDecodeError:
        return value, set()
    secrets: set[str] = set()
    redacted, changed = _redact_json_value(document, secrets)
    if not changed:
        return value, set()
    return json.dumps(redacted, ensure_ascii=False, separators=(",", ":")), secrets


def _redact_structured_json(value: str) -> tuple[str, set[str]]:
    replacement, secrets = _redact_json_document(value)
    if replacement != value:
        return replacement, secrets

    prefix, separator, document = value.partition("=")
    if not separator:
        return value, set()
    replacement, secrets = _redact_json_document(document)
    if replacement == document:
        return value, set()
    return f"{prefix}={replacement}", secrets


def _redact_argv_and_collect_secrets(argv: list[str]) -> tuple[list[str], set[str]]:
    redacted: list[str] = []
    secrets: set[str] = set()
    next_kind: str | None = None
    command_index = _effective_command_index(argv)
    for argument_index, part in enumerate(argv):
        if next_kind == "secret":
            redacted.append("<redacted>")
            _add_secret(secrets, part)
            next_kind = None
            continue
        if next_kind == "header":
            replacement, header_secrets = _redact_header(part)
            redacted.append(replacement)
            secrets.update(header_secrets)
            next_kind = None
            continue

        lowered = part.casefold()
        if _is_header_flag(part):
            redacted.append(part)
            next_kind = "header"
            continue
        if lowered in SECRET_FLAG_NAMES:
            redacted.append(part)
            next_kind = "secret"
            continue

        flag, equals, assignment_value = part.partition("=")
        if equals and _is_header_flag(flag):
            replacement, header_secrets = _redact_header(assignment_value)
            redacted.append(f"{flag}={replacement}")
            secrets.update(header_secrets)
            continue
        if equals and (flag.casefold() in SECRET_FLAG_NAMES or _is_sensitive_name(flag)):
            redacted.append(f"{flag}=<redacted>")
            _add_secret(secrets, assignment_value)
            continue

        if part.startswith("-H") and len(part) > 2:
            replacement, header_secrets = _redact_header(part[2:])
            if header_secrets:
                redacted.append(f"{part[:2]}{replacement}")
                secrets.update(header_secrets)
                continue

        short_flag_mode = _short_secret_flag_mode(
            argv,
            command_index=command_index,
            argument_index=argument_index,
            flag=lowered[:2],
        )
        if len(part) == 2 and short_flag_mode == "separate-or-attached":
            redacted.append(part)
            next_kind = "secret"
            continue

        if not part.startswith("--"):
            if short_flag_mode is not None and len(part) > 2:
                value = part[2:]
                prefix = part[:2]
                if value.startswith("="):
                    prefix += "="
                    value = value[1:]
                redacted.append(f"{prefix}<redacted>")
                _add_secret(secrets, value)
                continue

        replacement, json_secrets = _redact_structured_json(part)
        if replacement != part:
            redacted.append(replacement)
            secrets.update(json_secrets)
            continue

        if part.startswith("--") and "=" not in part and _is_sensitive_name(part):
            redacted.append(part)
            next_kind = "secret"
            continue

        replacement, header_secrets = _redact_header(part)
        if header_secrets:
            redacted.append(replacement)
            secrets.update(header_secrets)
            continue

        replacement, url_secrets = _redact_url_userinfo(part)
        if url_secrets:
            redacted.append(replacement)
            secrets.update(url_secrets)
            continue

        replacement, assignment_secrets = _redact_embedded_assignments(part)
        if assignment_secrets:
            redacted.append(replacement)
            secrets.update(assignment_secrets)
            continue

        redacted.append(part)
    return redacted, secrets


def redact_argv(argv: list[str]) -> list[str]:
    return _redact_argv_and_collect_secrets(argv)[0]


def redact_text_using_secret_values(value: str, secrets: set[str]) -> str:
    candidates = sorted((secret for secret in secrets if secret), key=len, reverse=True)
    if not candidates:
        return value
    marker_guard = "\x00HEXAHARNESS_REDACTION_MARKER\x00"
    while marker_guard in value or any(marker_guard in secret for secret in secrets):
        marker_guard += "_"
    protected = value.replace("<redacted>", marker_guard)
    pattern = re.compile("|".join(re.escape(secret) for secret in candidates))
    return pattern.sub("<redacted>", protected).replace(marker_guard, "<redacted>")


def sensitive_environment_values(environment: Mapping[str, str]) -> set[str]:
    return {value for name, value in environment.items() if value and _is_sensitive_name(name)}


def redact_text_using_argv(value: str, argv: list[str]) -> str:
    """Remove secret-bearing argv values, including values echoed without their flags."""
    redacted, secrets = _redact_argv_and_collect_secrets(argv)
    replacements = {
        original: replacement
        for original, replacement in zip(argv, redacted, strict=True)
        if original and original != replacement
    }
    for secret in secrets:
        replacements.setdefault(secret, "<redacted>")
    result = value
    if replacements:
        candidates = sorted(replacements, key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(item) for item in candidates))
        result = pattern.sub(lambda match: replacements[match.group(0)], result)
    # Every collected secret is already part of the one-pass replacement table.
    # A second pass could interpret text inside our own ``<redacted>`` marker as
    # another secret (for example, when the secret itself is ``redacted``).
    return result


def compact_tail(value: str, *, limit: int = 2000) -> str:
    normalized = value.strip()
    if len(normalized) <= limit:
        return normalized
    return f"...<truncated>\n{normalized[-limit:]}"
