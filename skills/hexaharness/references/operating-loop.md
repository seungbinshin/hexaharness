# Operating loop

Read this reference for material multi-step work inside an initialized project.

## Start and recover

The primary skill resolves its bundled launcher as `<HEXA>`. Keep all task IDs and routine command
details internal unless they are needed for recovery or audit.

1. Run `<HEXA> doctor --root <repo>`.
2. Run `<HEXA> status --root <repo>` and resume a matching active checkpoint when one exists.
3. Otherwise run `<HEXA> start --root <repo> "<observable goal>"` and retain the returned task ID.
4. After a restart, run `<HEXA> resume --root <repo> <task-id>` and continue from `next_step`.

## Execute

- Check a command without running it: `<HEXA> policy-check --root <repo> -- <argv...>`.
- Run an allowed command: `<HEXA> run --root <repo> <task-id> -- <argv...>`.
- An ask decision exits with status 3 and reports one of two boundaries. For `agent-review`, inspect
  the exact argv and implementation; rerun with `--reviewed` only for project-local, reversible work
  already authorized by the user's outcome. For `human-approval`, obtain approval for the exact
  command and target before rerunning with `--approved`. Neither flag can bypass a deny decision,
  and `--reviewed` cannot authorize an external or hard-to-reverse action.
- A deny decision exits with status 2. Do not work around it; change the task or approved policy.
- Commands are argument arrays and never shell strings. Do not add pipes, redirects, interpolation,
  or compound shell expressions as a shortcut.

Checkpoint after meaningful steps:

```text
<HEXA> checkpoint <task-id> --completed "<step>" --next "<next step>" \
  --artifact <path> --tokens <host-reported-count> --cost-usd <host-reported-cost>
```

Do not invent usage values. Record zero when the host exposes no measurement.

## Cross an approval boundary

After local verification, stage one exact external action without executing it:

```text
<HEXA> prepare-external <task-id> -- <argv...>
```

The runtime stores a redacted display plus a one-time nonce and local-key HMAC-SHA-256 binding to
the task ID, protocol phase, and full argv. Do not construct or edit `PENDING_EXTERNAL_ACTION`,
`RECONCILE_EXTERNAL_ACTION`, or
`VERIFY_EXTERNAL_ACTION_RESULT` markers by hand. Ask the user to approve the exact action and target;
after approval, run the identical argv once with `<HEXA> run --approved`. The action has no automatic
retry. If the user declines, record the reason and retire the pending nonce with `<HEXA> checkpoint
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

Run `<HEXA> verify <task-id>` after changes. Repair a failed result within the retry budget, using the
retained output path as evidence. Use `<HEXA> complete <task-id> --artifact <path>` only when at
least one regular project-output file exists outside `.hexaharness` and no requested action remains;
completion reruns required sensors and refuses to advance on failure.

When execution cannot continue, preserve the checkpoint and use `<HEXA> stop <task-id> --reason
"<reason>"`. A project-wide `<HEXA> stop` creates `.hexaharness/STOP`, which blocks subsequent command
execution until an operator reviews state and explicitly clears it during `<HEXA> resume`.

Use `<HEXA> prune` to preview expired terminal checkpoints, logs, and proposals. Review the candidate
paths before running `<HEXA> prune --apply`; active task state and tracked guide records are never
pruned.
