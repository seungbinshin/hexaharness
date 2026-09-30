# Windows host sandbox failures

## Scope: host failure, not a HexaHarness permission exception

`CreateProcessWithLogonW failed:1385` / `ERROR_LOGON_TYPE_NOT_GRANTED` means Windows refused
the requested logon type. When Codex reports it before launching the command, HexaHarness and
the project command may not have started at all. Changing project code, HexaHarness path grants,
or `--approved` cannot repair that host boundary. `doctor` checks project configuration; a passing
result does not certify the Codex sandbox or Windows account policy.

The relevant HexaHarness responsibility is the **host agent's failure response**: recognize the
layer, stop futile retries, preserve work, and request only the evidence or authority actually
needed. This guide does not change Windows, Codex configuration, accounts, or privileges.

## Separate observation from explanation

| Observation | What it establishes / what remains unknown |
|---|---|
| Sandboxed commands report 1385; an approved execution route works | A host execution-path difference, not proof of a project bug or permission to bypass the failing route |
| `[windows] sandbox = "elevated"` is present | A configured backend, not proof that setup completed or that a running process uses this file's current contents |
| Failures recur after app startup/update | A useful timeline; it does not alone prove which process rewrote configuration or whether stale settings caused the failure |
| Helper accounts are reported as `CodexSandboxOffline` / `CodexSandboxOnline` | Verify the actual account/SID involved; do not assume both accounts or one particular logon right caused this machine's failure |

Do not automatically delete the Windows section, repeatedly rewrite global configuration, add
allowlisted command prefixes, wrap commands in `uv run` / `cmd /c`, or request full access just to
get past this error. Do not grant account rights, remove deny rules, or change firewall policy as
an incidental project repair. Existing approvals remain specific to their original action.

## Minimal agent response

1. Stop unchanged retries after the first clear host-logon failure. Record the error signature,
   timestamp, intended command with secrets removed, and whether the host launched HexaHarness.
   A nonzero project test result without this evidence is a different failure class.
2. Preserve the existing checkpoint and pending external-action markers. If the runtime can be
   reached through a permitted, in-scope route, record the blocker and pause the task. If the host
   cannot launch it, report that durable recording was unavailable; do not invent a saved event
   or change execution routes to evade the restriction.
   Pause the affected execution, not independent planning or review of already available material.
   Continuing an independently authorized, unrelated action is different from moving the blocked
   command into a permitted wrapper, shell, or privilege context; do not do the latter.
3. With permission for the specific diagnostic data, collect only the Windows build, Codex app
   and executable versions, configured/effective sandbox mode, relevant error excerpt, and the
   before/after timestamps. Inspect only relevant fields, not an entire configuration dump.
   Prefer the host's current-session settings or diagnostic log for the effective mode; if it
   cannot be established, record it as unknown rather than infer it from `config.toml`.
   The official diagnostic log is `CODEX_HOME/.sandbox/sandbox.log`; sanitize selected excerpts.
   Never collect or share `CODEX_HOME/.sandbox-secrets/`, passwords, tokens, or unrelated files.
4. For a rights diagnosis, correlate the failure with Security event **4625**, when available:
   target account/SID, logon type, status/substatus, and caller process. Audit data access can itself
   require approval. Absence of an event does not prove that rights are correct.
5. After an authorized host repair, verify that the intended configuration is actually loaded.
   A file edit alone does not prove this for an already-running host. Use a harmless command in
   the same sandboxed route that failed; an approved/outside-sandbox success is not recovery
   evidence. Check again after a relevant app update or policy refresh, then resume the preserved
   task. If an external mutation's result is uncertain, reconcile it before retrying.
   For example, test PowerShell `Get-Location` in the project through the normal sandbox tool,
   without requesting outside-sandbox execution; confirm which route the host actually selected.

## Choosing a host remedy

Prefer repairing the configured sandbox through the host's supported setup and the device's
authorized policy owner. "IT" means whoever manages that computer's Windows/security policy:
the organization's endpoint/security administrators on a managed device, or the authorized local
administrator on an unmanaged personal device. It is not a HexaHarness component.

Do not infer **SeBatchLogonRight** solely from error 1385. Microsoft documents local-logon
permission for `CreateProcessWithLogonW`; the actual account and logon type determine the rights
to inspect. Correlate the installed host's evidence before proposing a policy change:

| Observed logon type | Rights to review, including inherited group rules |
|---|---|
| 2 — Interactive | `SeInteractiveLogonRight` and `SeDenyInteractiveLogonRight` |
| 4 — Batch | `SeBatchLogonRight` and `SeDenyBatchLogonRight` |
| Other / unknown | Identify the actual type; do not guess a right or grant several |

A corresponding deny right overrides an allow right. Effective domain/MDM policy and group
membership matter; a local grant may not be sufficient or durable. Any approved change should
target only the verified account/right, retain existing assignments, and include rollback and
post-policy-refresh verification. HexaHarness does not perform this administrative change.

Current official OpenAI documentation supports `elevated` and `unelevated`; deleting the section
is not the only option. `unelevated` uses a restricted current-user token and weaker isolation,
especially for network controls. Treat it as a separately authorized temporary fallback **only
if the device's governing policy permits it**. Administrators can restrict allowed implementations
through `requirements.toml`. Do not override such a restriction or silently switch backends.

## Evidence and verification limits

This guidance addresses the reported pattern, not a verified diagnosis of a particular PC.
HexaHarness tests and CI do not reproduce the Codex app's helper-account setup, enterprise policy,
configuration rewriting, or process lifetime. A documentation update is not a Windows repair.

Official references checked 2026-09-30:

- [OpenAI: Windows sandbox modes, 1385, diagnostics and enterprise restrictions](https://developers.openai.com/codex/windows/)
- [Microsoft: CreateProcessWithLogonW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createprocesswithlogonw)
- [Microsoft: account logon rights and deny precedence](https://learn.microsoft.com/en-us/windows/win32/secauthz/account-rights-constants)
- [Microsoft: event 4625 and logon types](https://learn.microsoft.com/en-us/windows/security/threat-protection/auditing/event-4625)
