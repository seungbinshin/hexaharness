from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from hexaharness.models import HarnessConfig, PolicyDecision


@dataclass(frozen=True)
class PolicyOutcome:
    decision: PolicyDecision
    reason: str
    requires_human_approval: bool = False


def _matches_prefix(argv: list[str], prefix: list[str]) -> bool:
    return len(argv) >= len(prefix) and argv[: len(prefix)] == prefix


def _command_name(value: str) -> str:
    name = Path(value).name.casefold()
    return name.removesuffix(".exe")


def _env_nested_argv(argv: list[str]) -> list[str] | None:
    """Return the raw command nested under ``env``, if one can be parsed safely."""
    if not argv or _command_name(argv[0]) != "env":
        return None
    index = 1
    options_with_values = {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            index += 1
            break
        if argument in options_with_values:
            index += 2
            continue
        if argument.startswith(("-C", "--chdir=", "-S", "--split-string=")):
            index += 1
            continue
        if argument.startswith("-") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argument):
            index += 1
            continue
        break
    return argv[index:] or None


def _timeout_nested_argv(argv: list[str]) -> list[str] | None:
    """Return the command nested under GNU/BusyBox ``timeout`` when present."""
    if not argv or _command_name(argv[0]) != "timeout":
        return None
    index = 1
    options_with_values = {"-k", "--kill-after", "-s", "--signal", "-t"}
    terminal_options = {"--help", "--version"}
    while index < len(argv):
        argument = argv[index]
        if argument in terminal_options:
            return None
        if argument == "--":
            index += 1
            break
        if argument in options_with_values:
            index += 2
            continue
        if argument.startswith(("--kill-after=", "--signal=")):
            index += 1
            continue
        if re.fullmatch(r"-(?:k|s|t).+", argument):
            index += 1
            continue
        if argument.startswith("-"):
            index += 1
            continue
        break
    if index >= len(argv):
        return None
    return argv[index + 1 :] or None


def _nice_nested_argv(argv: list[str]) -> list[str] | None:
    """Return the command nested under ``nice`` while respecting adjustment options."""
    if not argv or _command_name(argv[0]) != "nice":
        return None
    index = 1
    terminal_options = {"--help", "--version"}
    while index < len(argv):
        argument = argv[index]
        if argument in terminal_options:
            return None
        if argument == "--":
            index += 1
            break
        if argument in {"-n", "--adjustment"}:
            index += 2
            continue
        if argument.startswith(("-n", "--adjustment=")) or re.fullmatch(r"-\d+", argument):
            index += 1
            continue
        if argument.startswith("-"):
            index += 1
            continue
        break
    return argv[index:] or None


def _stdbuf_nested_argv(argv: list[str]) -> list[str] | None:
    """Return a command nested under GNU ``stdbuf`` when its options are unambiguous."""
    if not argv or _command_name(argv[0]) != "stdbuf":
        return None
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument in {"--help", "--version"}:
            return None
        if argument == "--":
            return argv[index + 1 :] or None
        if argument in {"-i", "-o", "-e", "--input", "--output", "--error"}:
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        if re.fullmatch(r"-[ioe].+", argument) or argument.startswith(
            ("--input=", "--output=", "--error=")
        ):
            index += 1
            continue
        if argument.startswith("-"):
            return None
        return argv[index:]
    return None


def _nohup_nested_argv(argv: list[str]) -> list[str] | None:
    """Return a command nested under ``nohup`` without guessing at unknown options."""
    if not argv or _command_name(argv[0]) != "nohup" or len(argv) < 2:
        return None
    if argv[1] in {"--help", "--version"}:
        return None
    if argv[1] == "--":
        return argv[2:] or None
    if argv[1].startswith("-"):
        return None
    return argv[1:]


def _setsid_nested_argv(argv: list[str]) -> list[str] | None:
    """Return a command nested under util-linux ``setsid``."""
    if not argv or _command_name(argv[0]) != "setsid":
        return None
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument in {"--help", "--version"}:
            return None
        if argument == "--":
            return argv[index + 1 :] or None
        if argument in {"--ctty", "--fork", "--wait"} or re.fullmatch(r"-[cfw]+", argument):
            index += 1
            continue
        if argument.startswith("-"):
            return None
        return argv[index:]
    return None


def _ionice_nested_argv(argv: list[str]) -> list[str] | None:
    """Return an ``ionice`` command form, excluding its process-retargeting forms."""
    if not argv or _command_name(argv[0]) != "ionice":
        return None
    index = 1
    process_selectors = {"-p", "--pid", "-P", "--pgid", "-u", "--uid"}
    value_options = {"-c", "--class", "-n", "--classdata"}
    while index < len(argv):
        argument = argv[index]
        if argument in {"-h", "--help", "-V", "--version"}:
            return None
        if argument == "--":
            return argv[index + 1 :] or None
        if (
            argument in process_selectors
            or any(
                argument.startswith(f"{option}=")
                for option in process_selectors
                if option.startswith("--")
            )
            or re.fullmatch(r"-(?:p|P|u).+", argument)
        ):
            return None
        if argument in value_options:
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        if argument.startswith(("--class=", "--classdata=")) or re.fullmatch(
            r"-(?:c|n).+", argument
        ):
            index += 1
            continue
        if argument in {"-t", "--ignore"}:
            index += 1
            continue
        if argument.startswith("-"):
            return None
        return argv[index:]
    return None


def _taskset_nested_argv(argv: list[str]) -> list[str] | None:
    """Return the command after a ``taskset`` affinity mask, excluding PID retargeting."""
    if not argv or _command_name(argv[0]) != "taskset":
        return None
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument in {"-h", "--help", "-V", "--version"}:
            return None
        if argument == "--":
            index += 1
            break
        if argument == "--pid" or (
            argument.startswith("-") and not argument.startswith("--") and "p" in argument[1:]
        ):
            return None
        if argument in {"-a", "--all-tasks", "-c", "--cpu-list"} or re.fullmatch(
            r"-[ac]+", argument
        ):
            index += 1
            continue
        if argument.startswith("-"):
            return None
        break
    if index + 1 >= len(argv):
        return None
    return argv[index + 1 :]


def _uv_run_nested_argv(argv: list[str]) -> list[str] | None:
    """Return the simple, option-free command forms of ``uv run``."""
    if len(argv) < 3 or _command_name(argv[0]) != "uv" or argv[1] != "run":
        return None
    if argv[2] == "--":
        return argv[3:] or None
    if argv[2].startswith("-"):
        return None
    return argv[2:]


def _multicall_nested_argv(argv: list[str]) -> list[str] | None:
    """Return the applet invocation nested under a BusyBox-style multicall binary.

    BusyBox and Toybox dispatch their first non-option argument as an executable applet.  Treat
    that applet exactly like a directly invoked command so a launcher cannot hide an intrinsic
    deny or an external-action gate.  Launcher options are deliberately left opaque: they differ
    between implementations and must be human-gated instead of being guessed at here.
    """
    if not argv or _command_name(argv[0]) not in {"busybox", "toybox"}:
        return None
    if len(argv) < 2 or argv[1].startswith("-"):
        return None
    return argv[1:]


def _transparent_launcher_nested_argv(argv: list[str]) -> list[str] | None:
    command = _command_name(argv[0]) if argv else ""
    if command == "env":
        return _env_nested_argv(argv)
    if command == "timeout":
        return _timeout_nested_argv(argv)
    if command == "nice":
        return _nice_nested_argv(argv)
    if command == "stdbuf":
        return _stdbuf_nested_argv(argv)
    if command == "nohup":
        return _nohup_nested_argv(argv)
    if command == "setsid":
        return _setsid_nested_argv(argv)
    if command == "ionice":
        return _ionice_nested_argv(argv)
    if command == "taskset":
        return _taskset_nested_argv(argv)
    if command == "uv":
        return _uv_run_nested_argv(argv)
    if command in {"busybox", "toybox"}:
        return _multicall_nested_argv(argv)
    return None


def _effective_argv(argv: list[str]) -> list[str]:
    """Normalize executables and recursively unwrap transparent command launchers."""
    if not argv:
        return []
    effective = [*argv]
    effective[0] = _command_name(effective[0])
    seen: set[tuple[str, ...]] = set()
    while tuple(effective) not in seen:
        seen.add(tuple(effective))
        nested = _transparent_launcher_nested_argv(effective)
        if nested is None:
            break
        effective = [*nested]
        effective[0] = _command_name(effective[0])
    return effective


def _risk_command_shapes(*commands: list[str]) -> list[list[str]]:
    """Return normalized command suffixes used only for deny and human-gate classification.

    An opaque launcher can put the executable after flags or runner-specific operands.  Allowing
    every suffix would be unsafe, but examining them at the stricter deny/approval layers ensures
    a known risky command cannot be downgraded merely by adding an unfamiliar launcher prefix.
    """
    shapes: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for command in commands:
        for index in range(len(command)):
            shape = [*command[index:]]
            shape[0] = _command_name(shape[0])
            for candidate in (shape, _git_subcommand_argv(shape)):
                key = tuple(candidate)
                if key not in seen:
                    seen.add(key)
                    shapes.append(candidate)
    return shapes


GIT_GLOBAL_OPTIONS_WITH_VALUES = {
    "-C",
    "-c",
    "--attr-source",
    "--config-env",
    "--git-dir",
    "--namespace",
    "--super-prefix",
    "--work-tree",
}


def _git_subcommand_argv(argv: list[str]) -> list[str]:
    """Move a Git subcommand behind recognized global options into position one.

    Git accepts global options such as ``-C`` before ``push`` or ``send-pack``. Policy rules are
    expressed against the subcommand, so leaving those options in place would let a remote mutation
    fall through to the lower agent-review boundary.
    """
    if not argv or argv[0] != "git":
        return argv

    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            index += 1
            break
        if argument in GIT_GLOBAL_OPTIONS_WITH_VALUES:
            if index + 1 >= len(argv):
                return argv
            index += 2
            continue
        if argument.startswith(("-C", "-c")) and len(argument) > 2:
            index += 1
            continue
        if any(
            argument.startswith(f"{option}=")
            for option in GIT_GLOBAL_OPTIONS_WITH_VALUES
            if option.startswith("--")
        ):
            index += 1
            continue
        if argument.startswith("-"):
            index += 1
            continue
        break

    if index >= len(argv):
        return argv
    return ["git", argv[index], *argv[index + 1 :]]


def _long_option_matches(arguments: set[str], full_name: str) -> bool:
    """Match Git's accepted long-option abbreviations and `--flag=value` forms."""
    for argument in arguments:
        name = argument.split("=", 1)[0]
        if name.startswith("--") and len(name) > 2 and full_name.startswith(name):
            return True
    return False


LOCAL_PATH_SCHEMES = {"file", "link", "patch", "portal", "workspace"}
LOCAL_LOCATOR_PATTERN = re.compile(
    r"(?i)(?:^|[\s@=])(?P<locator>(?:(?:[a-z0-9+.-]+\+)?file|link|patch|portal|workspace):.+)$"
)


def _local_locator_paths(value: str) -> list[str] | None:
    """Extract filesystem paths from package-manager and file URL locators.

    Remote URL arguments are not project paths, but ``file:``, ``link:``, and related dependency
    locators are. Treating every ``://`` value as remote allowed absolute local file URLs to bypass
    the project-root boundary.
    """
    parsed = urlsplit(value)
    scheme = parsed.scheme.casefold()
    if scheme not in LOCAL_PATH_SCHEMES and not scheme.endswith("+file"):
        match = LOCAL_LOCATOR_PATTERN.search(value)
        if match is None:
            return None
        parsed = urlsplit(match.group("locator"))
        scheme = parsed.scheme.casefold()

    raw_path = unquote(parsed.path)
    if parsed.netloc:
        raw_path = f"//{unquote(parsed.netloc)}{raw_path}"
    paths = [raw_path] if raw_path else []
    if scheme == "patch" and parsed.fragment:
        paths.append(unquote(parsed.fragment))
    return paths


def _candidate_paths(value: str) -> list[str]:
    local_locators = _local_locator_paths(value)
    if local_locators is not None:
        return local_locators
    if "://" in value:
        return []
    return [value]


def _long_option_value_matches(argv: list[str], full_name: str, expected: str) -> bool:
    """Match a long option's split or attached value, including accepted abbreviations."""
    for index, argument in enumerate(argv):
        name, separator, value = argument.partition("=")
        matches_name = name.startswith("--") and len(name) >= 4 and full_name.startswith(name)
        if not matches_name:
            continue
        if separator and value.casefold() == expected.casefold():
            return True
        if not separator and index + 1 < len(argv):
            if argv[index + 1].casefold() == expected.casefold():
                return True
    return False


def _command_path_escape_reason(argv: list[str], project_root: Path) -> str | None:
    """Reject explicit command arguments that resolve outside the configured project root."""
    root = project_root.resolve()
    is_env = bool(argv) and _command_name(argv[0]) == "env"
    for argument in argv[1:]:
        if is_env and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argument):
            candidate = argument.split("=", 1)[1]
        elif argument.startswith("-C") and argument != "-C":
            candidate = argument[2:]
        elif argument.startswith("-") and "=" in argument:
            candidate = argument.split("=", 1)[1]
        else:
            candidate = argument
        candidate = candidate.removeprefix("@")
        for path_candidate in _candidate_paths(candidate):
            if not path_candidate or path_candidate.startswith("-"):
                continue
            looks_like_path = (
                path_candidate.startswith((".", "~", "/", "\\"))
                or "/" in path_candidate
                or "\\" in path_candidate
            )
            if not looks_like_path:
                continue
            expanded = Path(path_candidate).expanduser()
            resolved = expanded.resolve() if expanded.is_absolute() else (root / expanded).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                return f"command argument escapes the configured project root: {argument}"
    return None


def _explicit_executable_reason(
    config: HarnessConfig, argv: list[str], project_root: Path
) -> str | None:
    """Require review for path-qualified executable aliases not explicitly trusted."""
    if not argv or not any(separator in argv[0] for separator in ("/", "\\")):
        return None
    if any(_matches_prefix(argv, prefix) for prefix in config.policy.allow_execute):
        return None

    executable = Path(argv[0]).expanduser()
    resolved = (
        executable.resolve() if executable.is_absolute() else (project_root / executable).resolve()
    )
    trusted_binary_dirs = {
        Path("/bin").resolve(),
        Path("/usr/bin").resolve(),
        Path("/usr/local/bin").resolve(),
        Path("/opt/homebrew/bin").resolve(),
    }
    if resolved.parent in trusted_binary_dirs:
        return None
    return f"path-qualified executable is not explicitly allow-listed: {argv[0]}"


def _inline_execution_flag_present(argv: list[str], flags: set[str]) -> bool:
    for argument in (argument.casefold() for argument in argv[1:]):
        for flag in flags:
            if argument == flag or (
                (flag.startswith("--") and argument.startswith(f"{flag}="))
                or (
                    flag.startswith("-")
                    and not flag.startswith("--")
                    and argument.startswith(flag)
                    and len(argument) > len(flag)
                )
            ):
                return True
    return False


def _code_wrapper_reason(argv: list[str], *, generic_inline: bool = True) -> str | None:
    """Detect command wrappers whose payload cannot be classified from argv alone."""
    if not argv:
        return None
    command_name = _command_name(argv[0])
    if command_name in {"parallel", "xargs"}:
        return "argument-driven command wrappers can conceal an external mutation"
    if command_name in {"busybox", "toybox"} and _multicall_nested_argv(argv) is None:
        return "opaque multicall launcher options can conceal an executable command"
    if command_name == "env":
        env_arguments = set(argv[1:])
        for argument in argv[1:]:
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argument):
                return "env variable assignments can change command identity or write scope"
            if (
                argument == "-S"
                or argument.startswith("-S")
                or _long_option_matches(env_arguments, "--split-string")
            ):
                return "env split-string can conceal an executable command"

    runner_arguments = {argument.casefold() for argument in argv[1:]}
    is_npm_exec = command_name == "npm" and bool({"exec", "x"}.intersection(runner_arguments))
    if (command_name == "npx" or is_npm_exec) and (
        any(argument == "-c" or argument.startswith("-c=") for argument in runner_arguments)
        or _long_option_matches(runner_arguments, "--call")
    ):
        return "package runner shell strings can conceal an external mutation"

    if argv[0] == "git" and any(
        argument in {"-c", "--config-env", "--exec-path"}
        or argument.startswith(("-c", "--config-env=", "--exec-path="))
        for argument in argv[1:]
    ):
        return "Git configuration overrides can change command execution behavior"

    code_execution_flags = {
        "bb": {"-e"},
        "sh": {"-c"},
        "bash": {"-c"},
        "bun": {"-e", "--eval", "-p", "--print"},
        "clojure": {"-e", "--eval"},
        "dash": {"-c"},
        "elixir": {"-e"},
        "erl": {"-eval"},
        "fish": {"-c", "--command"},
        "gosh": {"-e"},
        "groovy": {"-e"},
        "guile": {"-c", "--command"},
        "julia": {"-e", "--eval"},
        "ksh": {"-c"},
        "lua": {"-e"},
        "luajit": {"-e"},
        "zsh": {"-c"},
        "cmd": {"/c", "/k"},
        "node": {"-e", "--eval", "-p", "--print"},
        "perl": {"-e"},
        "php": {"-r"},
        "pwsh": {"-c", "-command"},
        "powershell": {"-c", "-command"},
        "python": {"-c"},
        "python3": {"-c"},
        "r": {"-e", "--expr"},
        "rscript": {"-e", "--expr"},
        "ruby": {"-e"},
        "swift": {"-e"},
    }
    command = command_name
    command = {
        "nodejs": "node",
        "py": "python3",
        "pyw": "python3",
        "pypy": "python3",
        "pypy3": "python3",
        "pythonw": "python3",
    }.get(command, command)
    versioned_interpreters = {
        r"lua(?:\d+(?:\.\d+)*)?": "lua",
        r"luajit(?:-?\d+(?:\.\d+)*)?": "luajit",
        r"perl(?:\d+(?:\.\d+)*)?": "perl",
        r"php(?:\d+(?:\.\d+)*)?": "php",
        r"python(?:\d+(?:\.\d+)*)?": "python3",
        r"pypy(?:3)?(?:\d+(?:\.\d+)*)?": "python3",
        r"ruby(?:\d+(?:\.\d+)*)?": "ruby",
    }
    for pattern, family in versioned_interpreters.items():
        if re.fullmatch(pattern, command):
            command = family
            break
    if command == "python3" and any(
        re.fullmatch(r"-[bBdEiIOPqRsSuvx]*c.*", argument) for argument in argv[1:]
    ):
        return "inline interpreter commands can conceal an external mutation"
    if _inline_execution_flag_present(argv, code_execution_flags.get(command, set())):
        return "inline interpreter commands can conceal an external mutation"
    if command in {"bash", "dash", "fish", "ksh", "sh", "zsh"} and any(
        argument.startswith("-") and not argument.startswith("--") and "c" in argument[1:]
        for argument in argv[1:]
    ):
        return "inline interpreter commands can conceal an external mutation"
    if command in {"bun", "deno"} and len(argv) >= 2 and argv[1].casefold() == "eval":
        return "inline interpreter commands can conceal an external mutation"
    transparent_wrappers = {
        "busybox",
        "env",
        "ionice",
        "nice",
        "nohup",
        "setsid",
        "stdbuf",
        "strace",
        "taskset",
        "timeout",
        "toybox",
        "uv",
    }
    if (
        generic_inline
        and command_name not in transparent_wrappers
        and _inline_execution_flag_present(argv, {"-c", "-e", "--command", "--eval", "--expr"})
    ):
        return "opaque inline-code flags can conceal an external mutation"
    return None


def _intrinsic_deny_reason(argv: list[str]) -> str | None:
    """Deny destructive command shapes even when flags or global options are reordered."""
    arguments = set(argv[1:])
    if argv[0] == "gh" and "auth" in arguments:
        shows_token = any(
            argument in {"--show-token", "-t"} or argument.startswith(("--show-token=", "-t="))
            for argument in arguments
        )
        if "token" in arguments or shows_token:
            return "GitHub authentication tokens must not be printed into retained output"
    if argv[0] == "rg":
        if any(argument == "--pre" or argument.startswith("--pre=") for argument in argv[1:]):
            return "ripgrep --pre executes an arbitrary preprocessing command"
        if any(
            argument == "--hostname-bin" or argument.startswith("--hostname-bin=")
            for argument in argv[1:]
        ):
            return "ripgrep --hostname-bin executes an arbitrary hostname command"
        if any(
            argument == "--search-zip"
            or (argument.startswith("-") and not argument.startswith("--") and "z" in argument[1:])
            for argument in argv[1:]
        ):
            return "ripgrep compressed search executes decompression programs from PATH"
    if argv[0] == "git" and any(
        _long_option_matches(arguments, option) for option in ("--ext-diff", "--textconv")
    ):
        return "Git external diff and text conversion hooks are not allowed in retained commands"
    if argv[0] == "git" and "reset" in arguments and _long_option_matches(arguments, "--hard"):
        return "git reset --hard discards working-tree and index changes"
    if argv[0] == "git" and "clean" in arguments:
        clean_flags = {argument for argument in arguments if argument.startswith("-")}
        if any("f" in flag.lstrip("-") for flag in clean_flags):
            return "git clean with force deletes untracked files"
    if argv[0] == "rm":
        short_flags = "".join(
            argument.lstrip("-")
            for argument in argv[1:]
            if argument.startswith("-") and not argument.startswith("--")
        )
        recursive = (
            "r" in short_flags
            or "R" in short_flags
            or _long_option_matches(arguments, "--recursive")
        )
        forced = "f" in short_flags or _long_option_matches(arguments, "--force")
        if recursive and forced:
            return "recursive forced removal is denied"
    if argv[0] == "kubectl" and "delete" in arguments:
        return "kubectl delete removes cluster resources"
    if argv[0] == "terraform" and "destroy" in arguments:
        return "terraform destroy removes managed infrastructure"
    return None


def _external_infrastructure_mutation_reason(argv: list[str]) -> str | None:
    """Require a human unless a common infrastructure operation is proven read-only."""
    if not argv:
        return None

    command = argv[0]
    arguments = [argument.casefold() for argument in argv[1:]]
    operation = arguments[0] if arguments else None
    read_only_operations = {
        "kubectl": {
            "api-resources",
            "api-versions",
            "cluster-info",
            "describe",
            "diff",
            "explain",
            "get",
            "logs",
            "top",
            "version",
        },
        "terraform": {"fmt", "graph", "output", "plan", "providers", "show", "validate", "version"},
        "helm": {
            "get",
            "history",
            "lint",
            "list",
            "search",
            "show",
            "status",
            "template",
            "verify",
            "version",
        },
        "pulumi": {"about", "logs", "preview", "version", "whoami"},
    }
    if command in read_only_operations:
        if operation is None or operation in read_only_operations[command]:
            return None
        return f"{command} operation is not proven read-only and may mutate external state"

    if command == "aws":
        service = operation
        service_operation = arguments[1] if len(arguments) >= 2 else None
        if service is None or service_operation is None:
            return None
        read_prefixes = ("batch-get-", "describe-", "get-", "head-", "list-", "lookup-", "search-")
        if service_operation.startswith(read_prefixes) or (
            service == "s3" and service_operation == "ls"
        ):
            return None
        return "aws operation is not proven read-only and may mutate external service state"

    if command == "az":
        # Azure CLI command paths are hierarchical and later operands can be arbitrary resource
        # names.  A trailing value such as ``show`` therefore cannot prove that the operation itself
        # is read-only (for example, ``az group delete --name show``).  Keep only the top-level
        # metadata command outside the human gate until exact service command paths are modeled.
        if operation == "version":
            return None
        return "az operation is not proven read-only and may mutate external service state"

    if command == "gcloud":
        # gcloud also places positional resource names after its hierarchical command path.
        # Treating the final token as the operation lets a mutating command target a resource named
        # ``show`` or ``list`` and evade the approval boundary.  These top-level metadata commands
        # are the only shapes that can be classified without a bundled command schema.
        if operation in {"info", "version"}:
            return None
        return "gcloud operation is not proven read-only and may mutate external service state"

    return None


def _intrinsic_ask_reason(argv: list[str]) -> str | None:
    if reason := _external_infrastructure_mutation_reason(argv):
        return reason

    if len(argv) >= 2 and argv[0] == "git":
        subcommand = argv[1]
        arguments = set(argv[2:])
        sensitive_long_options = {
            "commit": ("--amend",),
            "branch": ("--delete", "--force"),
            "tag": ("--delete", "--force"),
        }
        matched = {
            option
            for option in sensitive_long_options.get(subcommand, ())
            if _long_option_matches(arguments, option)
        }
        short_flags = {
            argument
            for argument in arguments
            if argument.startswith("-") and not argument.startswith("--")
        }
        if subcommand == "branch" and any(
            {"C", "d", "D", "f", "M"}.intersection(flag.lstrip("-")) for flag in short_flags
        ):
            matched.add(sorted(short_flags)[0])
        if subcommand == "tag" and any(
            {"d", "f"}.intersection(flag.lstrip("-")) for flag in short_flags
        ):
            matched.add(sorted(short_flags)[0])
        if matched:
            return f"git {subcommand} uses history-changing flag: {sorted(matched)[0]}"
        if subcommand in {"push", "send-pack"}:
            return f"git {subcommand} mutates a remote repository"
        if subcommand in {"rebase", "restore"}:
            return f"git {subcommand} can rewrite or discard local state"
        if subcommand == "stash" and {"clear", "drop"}.intersection(arguments):
            return "git stash deletion can discard recoverable work"

    safe_gh_prefixes = (
        ("gh", "--version"),
        ("gh", "auth", "status"),
        ("gh", "help"),
        ("gh", "status"),
        ("gh", "issue", "list"),
        ("gh", "issue", "status"),
        ("gh", "issue", "view"),
        ("gh", "pr", "checks"),
        ("gh", "pr", "diff"),
        ("gh", "pr", "list"),
        ("gh", "pr", "status"),
        ("gh", "pr", "view"),
        ("gh", "release", "list"),
        ("gh", "release", "view"),
        ("gh", "repo", "list"),
        ("gh", "repo", "view"),
        ("gh", "run", "list"),
        ("gh", "run", "view"),
        ("gh", "workflow", "list"),
        ("gh", "workflow", "view"),
    )
    if argv[0] == "gh":
        if any(tuple(argv[: len(prefix)]) == prefix for prefix in safe_gh_prefixes):
            return None
        return "GitHub command may mutate external state"

    external_prefixes = (
        ("npm", "publish"),
        ("npm", "pub"),
        ("npm", "unpublish"),
        ("npm", "deprecate"),
        ("npm", "dist-tag", "add"),
        ("npm", "dist-tag", "rm"),
        ("npm", "owner"),
        ("npm", "access"),
        ("npm", "token"),
        ("pnpm", "publish"),
        ("yarn", "npm", "publish"),
        ("uv", "publish"),
        ("cargo", "publish"),
        ("cargo", "yank"),
        ("cargo", "owner"),
        ("twine", "upload"),
    )
    if any(tuple(argv[: len(prefix)]) == prefix for prefix in external_prefixes):
        return "command publishes or changes an external package"

    if argv[0] in {"npm", "pnpm", "yarn"}:
        arguments = set(argv[1:])
        short_global = any(
            argument.startswith("-") and not argument.startswith("--") and "g" in argument[1:]
            for argument in argv[1:]
        )
        location_global = _long_option_value_matches(argv[1:], "--location", "global")
        if short_global or _long_option_matches(arguments, "--global") or location_global:
            return "global package installation writes outside the project"

    return None


def _unknown_sensitive_reason(argv: list[str]) -> str | None:
    """Recognize high-risk intent only after no trusted allow prefix matched."""
    sensitive_targets = {"delete", "deploy", "destroy", "publish", "push", "release"}
    targets = {
        Path(argument).stem.casefold()
        for argument in argv
        if argument and not argument.startswith("-")
    }
    if matched := targets.intersection(sensitive_targets):
        return f"command targets external or hard-to-reverse action: {sorted(matched)[0]}"
    return None


def evaluate_command(
    config: HarnessConfig,
    argv: list[str],
    *,
    project_root: Path | None = None,
) -> PolicyOutcome:
    if not argv:
        return PolicyOutcome(PolicyDecision.DENY, "empty commands are invalid")
    effective = _effective_argv(argv)
    classified = _git_subcommand_argv(effective)
    risk_shapes = _risk_command_shapes(argv, effective)
    trusted_shapes = (argv, effective, classified)
    if project_root is not None:
        for command_shape in (argv, effective):
            if reason := _command_path_escape_reason(command_shape, project_root):
                return PolicyOutcome(PolicyDecision.DENY, reason)
        executable_shapes = [argv]
        if nested := _env_nested_argv(argv):
            executable_shapes.append(nested)
        for command_shape in executable_shapes:
            if reason := _explicit_executable_reason(config, command_shape, project_root):
                return PolicyOutcome(PolicyDecision.ASK, reason, requires_human_approval=True)
    for shape in risk_shapes:
        if reason := _intrinsic_deny_reason(shape):
            return PolicyOutcome(PolicyDecision.DENY, reason)
    for prefix in config.policy.deny_execute:
        if any(_matches_prefix(shape, prefix) for shape in (*trusted_shapes, *risk_shapes)):
            return PolicyOutcome(PolicyDecision.DENY, f"matches deny prefix: {' '.join(prefix)}")
    exact_original_allow = any(argv == prefix for prefix in config.policy.allow_execute)
    exact_effective_allow = any(effective == prefix for prefix in config.policy.allow_execute)
    for shape in risk_shapes:
        configured_safe_command = any(
            prefix and _command_name(prefix[0]) == shape[0]
            for prefix in config.policy.allow_execute
        )
        if reason := _code_wrapper_reason(shape, generic_inline=not configured_safe_command):
            exact_shape_is_trusted = exact_original_allow or (
                shape == effective and exact_effective_allow
            )
            if exact_shape_is_trusted:
                continue
            return PolicyOutcome(PolicyDecision.ASK, reason, requires_human_approval=True)
    for shape in risk_shapes:
        if reason := _intrinsic_ask_reason(shape):
            return PolicyOutcome(PolicyDecision.ASK, reason, requires_human_approval=True)
    for prefix in config.policy.ask_execute:
        if any(_matches_prefix(shape, prefix) for shape in (*trusted_shapes, *risk_shapes)):
            return PolicyOutcome(
                PolicyDecision.ASK,
                f"matches approval prefix: {' '.join(prefix)}",
                requires_human_approval=True,
            )
    for prefix in config.policy.allow_execute:
        if any(_matches_prefix(shape, prefix) for shape in trusted_shapes):
            return PolicyOutcome(PolicyDecision.ALLOW, f"matches allow prefix: {' '.join(prefix)}")
    if reason := _unknown_sensitive_reason(classified):
        return PolicyOutcome(PolicyDecision.ASK, reason, requires_human_approval=True)
    return PolicyOutcome(config.policy.unknown_execute, "no configured command prefix matched")


def _matches_path(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def evaluate_path(
    config: HarnessConfig,
    project_root: Path,
    target: Path,
    *,
    write: bool,
) -> PolicyOutcome:
    root = project_root.resolve()
    resolved = target.resolve() if target.is_absolute() else (root / target).resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        return PolicyOutcome(PolicyDecision.DENY, "target escapes the configured project root")

    components = [part.casefold() for part in Path(relative).parts]
    basename = components[-1] if components else ""
    if write and relative == ".":
        return PolicyOutcome(PolicyDecision.DENY, "the project root itself is not a file target")
    if write and ".git" in components:
        return PolicyOutcome(PolicyDecision.DENY, "Git internal paths cannot be written directly")
    if write and (
        basename == ".env"
        or basename.startswith(".env.")
        or "credential" in basename
        or "secret" in basename
        or basename in {".npmrc", ".pypirc"}
    ):
        return PolicyOutcome(PolicyDecision.DENY, "sensitive configuration paths are denied")
    if write and _matches_path(relative, config.policy.deny_write_paths):
        return PolicyOutcome(PolicyDecision.DENY, "target matches a denied write path")
    if write and _matches_path(relative, config.policy.write_paths):
        return PolicyOutcome(PolicyDecision.ALLOW, "target matches an allowed write path")
    if write and _matches_path(relative, config.policy.ask_write_paths):
        return PolicyOutcome(
            PolicyDecision.ASK, "target requires write approval", requires_human_approval=True
        )
    if not write and _matches_path(relative, config.policy.read_paths):
        return PolicyOutcome(PolicyDecision.ALLOW, "target matches an allowed read path")
    return PolicyOutcome(
        PolicyDecision.ASK,
        "target is not covered by an explicit path rule",
        requires_human_approval=True,
    )
