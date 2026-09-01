from __future__ import annotations

import json

from hexaharness.io import redact_argv, redact_text_using_argv


def test_redact_argv_handles_common_credential_forms() -> None:
    argv = [
        "env",
        "GITHUB_TOKEN=env-secret",
        "curl",
        "--access-token",
        "access-secret",
        "-H",
        "Authorization: Bearer header-secret",
        "--header=Cookie: session=cookie-secret; csrf=csrf-secret",
        "--header",
        "Accept: application/json",
        "--user=api-user:user-password-secret",
        "https://x-access-token:url-token-secret@example.test/repository.git",
        "--split-string=API_TOKEN=split-secret command",
    ]

    redacted = redact_argv(argv)

    rendered = " ".join(redacted)
    for secret in (
        "env-secret",
        "access-secret",
        "header-secret",
        "cookie-secret",
        "csrf-secret",
        "user-password-secret",
        "url-token-secret",
        "split-secret",
    ):
        assert secret not in rendered
    assert "GITHUB_TOKEN=<redacted>" in redacted
    assert "Authorization: <redacted>" in redacted
    assert "--header=Cookie: <redacted>" in redacted
    assert "Accept: application/json" in redacted
    assert "--user=<redacted>" in redacted
    assert "https://<redacted>@example.test/repository.git" in redacted
    assert "--split-string=API_TOKEN=<redacted> command" in redacted


def test_redact_argv_preserves_ambiguous_short_flags_outside_secret_contexts() -> None:
    git_push = ["git", "push", "-u", "origin", "HEAD:main"]

    assert redact_argv(git_push) == git_push
    assert redact_text_using_argv("push origin HEAD:main", git_push) == "push origin HEAD:main"
    assert redact_argv(["git", "checkout", "-b", "release"]) == [
        "git",
        "checkout",
        "-b",
        "release",
    ]
    assert redact_argv(["ssh", "-p", "2222", "example.test"]) == [
        "ssh",
        "-p",
        "2222",
        "example.test",
    ]
    assert redact_argv(["docker", "run", "-p", "8080:80", "demo"]) == [
        "docker",
        "run",
        "-p",
        "8080:80",
        "demo",
    ]


def test_redact_argv_redacts_ambiguous_short_flags_in_secret_contexts() -> None:
    argv = [
        "env",
        "MODE=test",
        "curl",
        "-u",
        "api-user:user-password-secret",
        "-bsession=cookie-secret",
        "https://example.test",
    ]

    redacted = redact_argv(argv)

    assert redacted[4] == "<redacted>"
    assert redacted[5] == "-b<redacted>"
    assert "user-password-secret" not in redact_text_using_argv(
        "user-password-secret cookie-secret", argv
    )
    assert redact_argv(["mysql", "-pdatabase-password-secret", "example"]) == [
        "mysql",
        "-p<redacted>",
        "example",
    ]
    assert redact_argv(["docker", "login", "-p", "registry-password-secret", "registry.test"]) == [
        "docker",
        "login",
        "-p",
        "<redacted>",
        "registry.test",
    ]


def test_redact_argv_finds_secret_contexts_through_transparent_wrappers() -> None:
    cases = [
        (["timeout", "--foreground", "5", "curl", "-u", "timeout-secret"], "timeout-secret"),
        (["nice", "-n", "10", "curl", "-bcookie=nice-secret"], "nice-secret"),
        (["busybox", "curl", "-u", "busybox-secret"], "busybox-secret"),
        (["toybox", "curl", "-b", "cookie=toybox-secret"], "toybox-secret"),
        (["nohup", "curl", "-u", "nohup-secret"], "nohup-secret"),
        (["stdbuf", "-oL", "curl", "-u", "stdbuf-secret"], "stdbuf-secret"),
        (["setsid", "-f", "curl", "-u", "setsid-secret"], "setsid-secret"),
        (["ionice", "-c", "2", "curl", "-u", "ionice-secret"], "ionice-secret"),
        (["taskset", "-c", "0", "curl", "-u", "taskset-secret"], "taskset-secret"),
        (["strace", "-f", "curl", "-u", "strace-secret"], "strace-secret"),
        (["uv", "run", "--", "curl", "-u", "uv-secret"], "uv-secret"),
        (["npx", "--yes", "curl", "-u", "npx-secret"], "npx-secret"),
        (["python", "-I", "-m", "curl", "-u", "python-secret"], "python-secret"),
        (["timeout", "5", "mysql", "-pmysql-secret", "database"], "mysql-secret"),
        (
            ["nice", "docker", "login", "-p", "docker-secret", "registry.test"],
            "docker-secret",
        ),
    ]

    for argv, secret in cases:
        assert secret not in " ".join(redact_argv(argv))
        assert secret not in redact_text_using_argv(f"echoed {secret}", argv)


def test_redact_argv_preserves_non_secret_short_flags_through_wrappers() -> None:
    git_push = [
        "env",
        "-u",
        "DISPLAY",
        "timeout",
        "5",
        "nice",
        "git",
        "push",
        "-u",
        "origin",
        "HEAD:main",
    ]
    published_port = ["taskset", "-c", "0", "docker", "run", "-p", "8080:80", "demo"]

    assert redact_argv(git_push) == git_push
    assert redact_text_using_argv("origin HEAD:main", git_push) == "origin HEAD:main"
    assert redact_argv(published_port) == published_port


def test_redact_text_scrubs_individual_secret_values_without_reprocessing_markers() -> None:
    argv = [
        "env",
        "API_TOKEN=env-secret",
        "curl",
        "--access-token=access-secret",
        "-predacted",
        "-HAuthorization: Bearer header-secret",
        "--header",
        "Cookie: session=cookie-secret; csrf=csrf-secret",
    ]
    output = (
        "env-secret access-secret redacted header-secret cookie-secret csrf-secret\n"
        "API_TOKEN=env-secret Authorization: Bearer header-secret"
    )

    scrubbed = redact_text_using_argv(output, argv)

    for secret in (
        "env-secret",
        "access-secret",
        "header-secret",
        "cookie-secret",
        "csrf-secret",
    ):
        assert secret not in scrubbed
    assert "<redacted>" in scrubbed
    assert "<<redacted>>" not in scrubbed


def test_empty_secret_argument_does_not_create_an_empty_text_pattern() -> None:
    assert redact_argv(["tool", "--token", ""]) == ["tool", "--token", "<redacted>"]
    assert redact_text_using_argv("ordinary output", ["tool", "--token", ""]) == "ordinary output"


def test_redact_argv_recursively_redacts_structured_json_secrets() -> None:
    payload = json.dumps(
        {
            "name": "demo",
            "auth": {
                "client_secret": "nested-client-secret",
                "metadata": {"token": "deep-token-secret"},
            },
            "items": [{"label": "first", "api_key": "list-api-secret"}],
        },
        separators=(",", ":"),
    )
    assigned_payload = '--data={"name":"demo","access_token":"assigned-secret"}'
    argv = ["curl", "--data", payload, assigned_payload, "https://example.test"]

    redacted = redact_argv(argv)

    parsed = json.loads(redacted[2])
    assert parsed["name"] == "demo"
    assert parsed["auth"]["client_secret"] == "<redacted>"
    assert parsed["auth"]["metadata"]["token"] == "<redacted>"
    assert parsed["items"][0]["label"] == "first"
    assert parsed["items"][0]["api_key"] == "<redacted>"
    assert json.loads(redacted[3].partition("=")[2]) == {
        "name": "demo",
        "access_token": "<redacted>",
    }
    rendered = " ".join(redacted)
    for secret in (
        "nested-client-secret",
        "deep-token-secret",
        "list-api-secret",
        "assigned-secret",
    ):
        assert secret not in rendered
        assert secret not in redact_text_using_argv(f"command echoed {secret}", argv)


def test_redact_argv_preserves_ordinary_json_and_emits_valid_secret_json() -> None:
    ordinary = '{ "name": "demo", "enabled": true, "items": [1, 2] }'
    first_sensitive_key = '{"token":"secret-value","name":"demo"}'

    redacted = redact_argv(["tool", ordinary, first_sensitive_key])

    assert redacted[1] == ordinary
    assert json.loads(redacted[2]) == {"token": "<redacted>", "name": "demo"}
