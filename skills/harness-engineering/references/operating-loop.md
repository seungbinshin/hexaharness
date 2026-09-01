# Operating loop

Read this reference for material multi-step work inside an initialized project.

## Start and recover

1. Run `hexa doctor --root <repo>`.
2. Run `hexa status --root <repo>` and resume a matching active checkpoint when one exists.
3. Otherwise run `hexa start --root <repo> "<observable goal>"` and retain the returned task ID.
4. After a restart, run `hexa resume --root <repo> <task-id>` and continue from `next_step`.

## Execute

- Check a command without running it: `hexa policy-check --root <repo> -- <argv...>`.
- Run an allowed command: `hexa run --root <repo> <task-id> -- <argv...>`.
- An ask decision exits with status 3. Obtain approval for the exact command and target before
  rerunning with `--approved`.
- A deny decision exits with status 2. Do not work around it; change the task or approved policy.
- Commands are argument arrays and never shell strings. Do not add pipes, redirects, interpolation,
  or compound shell expressions as a shortcut.

Checkpoint after meaningful steps:

```text
hexa checkpoint <task-id> --completed "<step>" --next "<next step>" \
  --artifact <path> --tokens <host-reported-count> --cost-usd <host-reported-cost>
```

Do not invent usage values. Record zero when the host exposes no measurement.

## Verify and complete

Run `hexa verify <task-id>` after changes. Repair a failed result within the retry budget, using the
retained output path as evidence. Use `hexa complete <task-id> --artifact <path>` only when the
artifact is ready; completion reruns required sensors and refuses to advance on failure.

When execution cannot continue, preserve the checkpoint and use `hexa stop <task-id> --reason
"<reason>"`. A project-wide `hexa stop` creates `.hexaharness/STOP`, which blocks subsequent command
execution until an operator reviews state and explicitly clears it during `hexa resume`.

Use `hexa prune` to preview expired terminal checkpoints, logs, and proposals. Review the candidate
paths before running `hexa prune --apply`; active task state and tracked guide records are never
pruned.
