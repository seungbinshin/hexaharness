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
