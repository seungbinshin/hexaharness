# Security policy

## Supported versions

Security fixes are applied to the latest released minor version.

## Reporting

Do not open a public issue for a vulnerability that could expose secrets, bypass a policy decision,
escape the configured project root, or execute an unapproved command. Use GitHub's private security
advisory workflow after the repository is published.

Include the affected version, a minimal reproduction, the expected policy decision, and the actual
result. Never include live credentials or private command output.

## Security boundaries

HexaHarness is a local enforcement aid, not a replacement for the host operating system sandbox.
It enforces policy for commands executed through `hexa`; host agents must still honor their own
filesystem, network, and approval controls for actions performed outside the CLI.

The plugin-bundled launcher uses Python's standard library to create a private cache environment and
installs only exact versions listed in `runtime-requirements.txt`. It does not download an installer,
execute shell strings, or resolve a potentially different `hexa` command from `PATH`. First-use
dependency retrieval remains subject to the host's network controls.

The default generated policy allows project-local reversible work, requires human approval for
external publication and service mutations, and denies known destructive commands and sensitive
write paths. An unregistered command can use `--reviewed` only after agent inspection; that flag
cannot cross a recognized human-approval boundary. Repository owners should tighten the policy for
their threat model; a host sandbox may always be stricter.

Approval-gated actions are staged before execution with a redacted display, a one-time nonce, and a
local-key HMAC-SHA-256 binding to the task ID, protocol phase, and complete argument array. A copied
task marker, changed phase, changed argument, or reused nonce therefore cannot replay the staged
approval, while retained events do not expose an unkeyed digest of secret-bearing arguments. The
runtime records reconciliation state before executing the action and requires external verification
afterward, preventing an interrupted push or publication from being retried blindly.
