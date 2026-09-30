# Operating loop

Read this reference for material multi-step work inside an initialized project.

## Start and recover

The primary skill resolves its bundled launcher as `<HEXA>`. Keep all task IDs and routine command
details internal unless they are needed for recovery or audit.

1. Run `<HEXA> doctor --root <repo>`.
2. Run `<HEXA> status --root <repo>` and resume a matching active checkpoint when one exists.
3. Otherwise run `<HEXA> start --root <repo> --kind <change|review|release> "<observable goal>"`.
   `change` is the default; use `review` for findings and `release` for publication-only work.
   Keep the returned task ID internal. Choose the kind at task creation; do not relabel finished work.
4. After a restart, run `<HEXA> resume --root <repo> <task-id>` and continue from `next_step`.

## Execute

- Check a command without running it: `<HEXA> policy-check --root <repo> -- <argv...>`.
- Run an allowed command: `<HEXA> run --root <repo> <task-id> -- <argv...>`.
- An ask decision exits with status 3 and reports one of two boundaries. For `agent-review`, inspect
  the exact argv and implementation; rerun with `--reviewed` only for project-local, reversible work
  already authorized by the user's outcome. For `human-approval`, confirm that existing authorization covers the exact
  command and target, or ask for it, before rerunning with `--approved`. Neither flag can bypass a deny decision,
  and `--reviewed` cannot authorize an external or hard-to-reverse action.
- A deny decision exits with status 2. Do not work around it; change the task or approved policy.
- Commands are argument arrays and never shell strings. Do not add pipes, redirects, interpolation,
  or compound shell expressions as a shortcut.

For Windows host-logon error 1385, use the [host failure guide](../../../docs/windows.md).
HexaHarness approval flags cannot repair a host that rejects process creation. Preserve pending
actions, stop unchanged retries, and record the blocker only if the runtime is reachable through
an already permitted, in-scope route. Do not wrap commands in approved prefixes to evade the host
boundary, alter global sandbox settings, or grant Windows account rights as a project fix.

Checkpoint after meaningful steps:

```text
<HEXA> checkpoint <task-id> --completed "<step>" --next "<next step>" \
  --artifact <path> --tokens <host-reported-count> --cost-usd <host-reported-cost>
```

Do not invent usage values. Record zero when the host exposes no measurement.

## Approved external paths

Follow [external-access setup](../../../docs/external-access.md) for task-scoped exceptions.
Supply the same `--access-grant <id>` to `policy-check`, `prepare-external`, and `run`. Path checks
use explicit `--operation stat|list|read|create|replace|delete`; an allowed path query is not
authorization for arbitrary host edits. Grant-based writes execute only the reviewed command.
Otherwise read-only command grants run directly with `run --approved`, without staging a mutation.
A read-only file scope does not downgrade a command already classified as an external mutation.
The flags record existing authorization; they do not acquire it.

The optional [anchored transaction contract](../../../docs/external-transactions.md) adds exact
scratch directories and hardlink pairs. `mkdir`, `rmdir`, and `link` path queries are preflight only.
Existing scratch remnants need independent inspection and reviewed creation evidence before a
separate recovery write grant. Exact names must be bound before approval; a tool that still creates
undisclosed names is not yet compatible. Do not skip executor review because policy checks pass.

External stdout/stderr is discarded. Have the reviewed tool write only safe, selected verification
fields to project-local evidence. For an uncertain mutation, retain its reconciliation marker and
use a separate read-only grant to inspect state. Explicit reactivation preserves the marker and
budgets; do not rerun the issuer until reconciliation is recorded.

## Cross an approval boundary

After local verification, stage one exact external action without executing it:

```text
<HEXA> prepare-external <task-id> -- <argv...>
```

The runtime stores a redacted display plus a one-time nonce and local-key HMAC-SHA-256 binding to
the task ID, protocol phase, and full argv. Do not construct or edit `PENDING_EXTERNAL_ACTION`,
`RECONCILE_EXTERNAL_ACTION`, or
`VERIFY_EXTERNAL_ACTION_RESULT` markers by hand. Use existing authorization for the exact action and
target; if it is missing, ask immediately before execution. Once authorized, run the identical argv
once with `<HEXA> run --approved`. The action has no automatic retry. If the user declines, record
the reason and retire the pending nonce with `<HEXA> checkpoint
<task-id> --completed "<reason>" --cancel-external-action`; do not execute the action.
If policy changes after staging, the pending command cannot fall through to an ordinary allow path:
cancel it and restage under the current policy. A new deny decision remains blocking.

On a successful return, independently inspect the external result. After the verification phase has
begun, run `policy-check --task-id <task-id> --path <evidence> --write` before writing a project
evidence file, then record it with `checkpoint --completed <verification> --artifact <evidence>
--clear-next`. Pre-existing evidence cannot clear this boundary. A nonzero exit, timeout, emergency
stop, process interruption, or host interruption is an uncertain result because a remote system may
have changed partially. The task remains at reconciliation. Inspect external state first, observe and
write fresh evidence after reconciliation began, and record the outcome with `checkpoint --completed
<resolution> --artifact <evidence> --resolve-external-action`; do not assume the action failed and
repeat it.

## Verify and complete

Use `<HEXA> complete <task-id> --artifact <path>` for final verification; it runs required sensors
and refuses completion on failure. Use `verify` separately when intermediate feedback or a
pre-publication gate is needed. A normal failed local command leaves the task active for causal
repair. Repeated identical failures, exhausted retries, timeouts, and budget limits preserve state
and escalate.

Completion evidence must be fresh, attached to this task, and bound to a pre-write observation:

| Kind | Required output |
|---|---|
| `change` | A changed or new project file outside `.hexaharness` |
| `review` | A findings report, which may be a local runtime artifact, plus a completed review step |
| `release` | A receipt attached while verifying a successfully executed external action |

Do not edit source just to finish a review or release. A release receipt should identify the target,
published commit/version, and observed result. A cancelled or unresolved action cannot count as a
successful release. No kind may complete with an external action pending.

Commands and task-scoped sensors share the task's time, tool-call, and host-reported usage budgets.
Time spent at the durable pending-approval checkpoint is excluded from wall time. Reactivating a
task does not reset its consumed budget. Unscoped operator `verify` uses individual sensor timeouts.

When execution cannot continue, preserve the checkpoint and use `<HEXA> stop <task-id> --reason
"<reason>"`. A project-wide `<HEXA> stop` creates `.hexaharness/STOP`, which blocks subsequent command
execution until an operator reviews state and explicitly clears it during `<HEXA> resume`.

Use `<HEXA> prune` to preview expired terminal checkpoints, logs, and proposals. Review the candidate
paths before running `<HEXA> prune --apply`; active task state and tracked guide records are never
pruned.
