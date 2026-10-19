# Runtime discovery and integration contract

`hexa capabilities` returns one JSON object on stdout and exit code 0. It neither reads
project configuration nor creates files. This can be called from an empty directory or
beside an unsupported project schema. Diagnostics belong on stderr. Its schema version is
independent of the package version; consumers must reject unsupported schema versions and
must not infer capability from the package version alone.

The object names the runtime version, supported configuration read/write versions, improvement,
task-budget and host-handoff contract versions, supported evaluators and candidate data limits.
Boolean capability flags distinguish runtime support from host responsibilities. In particular,
`host_handoff: true` means request/receipt accounting is available, while
`provider_dispatch: false` means the runtime does not call a model. `provider_evidence` remains
`not-assessed`: discovery does not certify any provider or a project's test history.

Existing JSON commands retain their envelopes. Command exit code must be a strict integer;
consumers must not coerce booleans, strings or missing values to success. A subprocess exit of
zero alone does not establish task completion: inspect `status: completed`, every required
sensor's integer zero exit, passing outcome and current evidence. Policy checks return 2 for
deny and 3 for ask. Unsupported future configuration schemas must fail closed for mutations.

The library's internal Python helpers are not a promise to freeze every function signature.
The supported integration boundary is the CLI envelopes and explicitly versioned models in
`improvement_models.py`, plus the task-budget record described in `task-budgets.md`. Optional
improvement and task-budget fields remain absent in legacy wire records when unused.

PsOs and other hosts should discover an installed plugin's manifest, declared version and
expected file layout without launching bootstrap or network installation. Launch only the
exact verified launcher with an already-approved Python runtime; then use capabilities as an
additional protocol check. Keep host-reported observations separate from independently verified
evidence. Capability discovery does not relax the host's native approval or sandbox policy.

Upgrade by pinning a reviewed tag/commit and checking artifact digests. Keep the old installation
available until the new runtime passes the consumer's compatibility checks. Do not run two
versions against the same active project concurrently. Before rollback, preserve new task
records; an older runtime may reject records containing new optional contracts. Installations,
account changes and consumer updates remain separate authorized operations.
