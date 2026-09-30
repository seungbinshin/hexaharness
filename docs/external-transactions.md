# Exact scratch directories and hardlink pairs

Contents: [scope](#scope), [executor contract](#executor-contract),
[recovery](#recovery), [verification](#verification).

This opt-in extends [external grants](external-access.md). Existing scopes keep their semantics.
It is a cooperative authorization contract, not an I/O interceptor. Only a reviewed executor
implementing **anchored-v1** may use it. A successful preflight does not prove runtime safety.

## Scope

The first implementation supports POSIX ownership metadata and exact, pre-bound names only.
Choose the operation ID and all names before approval and include them in the exact command or its
reviewed input. An issuer which generates undisclosed names internally needs an adapter/change
before it can use this contract. No wildcard expansion, runtime enrollment of names, recursive
directory permission, or global hardlink exception is provided. On Windows these opt-ins fail
closed until an equivalent ownership/ACL and anchored-execution contract is implemented; ordinary
0.3.0 grants are unchanged.

Example fragment inside a grant (add `purpose`, an expiry within 24 hours, and other confirmed
dependencies). Paths and names are illustrative. The existing parent must be owned by the executing
user with mode `0700`; the existing common lock must be a single-link regular file with mode `0600`.
If the lock does not exist, creating it is a separate narrowly approved setup operation. Do not
change existing operational permissions automatically to satisfy preflight.

```json
{
  "paths": [
    {"path": "/srv/coach/common.lock", "operations": ["stat", "read"]}
  ],
  "transactions": [{
    "parent": "/srv/coach",
    "lock": "common.lock",
    "executor_contract": "anchored-v1",
    "scratch_directories": [{
      "name": ".probe-op42.scratch",
      "operations": ["stat", "mkdir", "rmdir"],
      "files": [
        {"name": "first", "operations": ["stat", "read", "create", "delete", "replace"]},
        {"name": "second", "operations": ["stat", "read", "create", "delete", "replace"]},
        {"name": "moved", "operations": ["stat", "read", "create", "delete", "replace"]}
      ]
    }],
    "hardlink_pairs": [{
      "source": {
        "name": "profile-op42.stage", "operations": ["stat", "read", "create", "delete"]
      },
      "destination": {
        "name": "profile.json", "operations": ["stat", "read", "link"]
      }
    }]
  }],
  "arguments": [{"path": "/srv/coach", "operation": "transaction"}]
}
```

Scratch and pair declarations are independent: either list may be omitted. Declare the journal
pair separately from the profile pair. Each name is one exact component. Names cannot overlap
between the lock, directories, or pairs, or be reauthorized through legacy path rules.
Protected components, denied writes, task/expiry/revocation, exact argv, signed scope, host permissions,
and the one-shot prepare/run/verify-or-reconcile protocol still apply.

| Operation | Meaning |
| --- | --- |
| `mkdir` | Create the one absent declared scratch directory with mode `0700` |
| `rmdir` | Remove that same, empty directory; never recursive deletion |
| `create` | Exclusively create the named regular file, mode `0600`; never overwrite |
| `replace` | Replace a declared scratch file; does not authorize in-place mutation of a pair |
| `delete` | Unlink only the declared name, with executor provenance/topology checks |
| `link` | Create the pair's destination from its source without overwriting; source needs `read` |
| `stat`, `read`, `list` | Metadata, file contents, or one scratch directory's entries respectively |

A scratch move consumes source `delete` and destination `create`; exchange consumes `replace`
on both names. The executor must verify both permissions before the operation. There is no generic
move/exchange CLI primitive. Pair members cannot request `replace`, directories cannot request
file operations, and legacy path rules cannot request `mkdir/rmdir/link`.

`policy-check --operation mkdir|rmdir|link` evaluates one declared operation without performing it.
For example, a `link` query requires the source-only state. A command preflight can approve future
declared stages before they exist; that does not authorize skipping the executor's later checks.
The transaction anchor represents the declared operations, including read-only inspection.

## Executor contract

Writing `executor_contract: anchored-v1` records the reviewed contract; it does not install wrappers
or certify an arbitrary existing issuer. Do not approve a command unless its implementation covers
every requirement below. Unsupported primitives or missing validation must fail before publication.

1. Acquire the declared common lock for the entire filesystem probe, publication, and cleanup.
   Locking the file does not authorize replacing, deleting, or writing its contents. Open parent,
   lock, and scratch objects without following symlinks. Compare path metadata with open-handle
   metadata and retain those handles. Validate owner, exact mode, device, and identity under lock
   before each relevant operation. Reject mount changes and objects with unexpected types.
2. Create the exact scratch name exclusively. Persist nonsecret creation evidence containing the
   parent, lock, directory, and each created file's device/inode/owner/mode before a recoverable
   interruption. A matching name or current owner alone is not provenance. Keep the receipt in the
   approved project evidence location; do not embed configuration, tokens, or key contents.
3. Create only declared children. Check actual-filesystem no-replace move/exchange support, preserve
   foreign entries, and stop publication if probing or cleanup fails. Before unlinking, compare each
   file against its creation evidence. Before `rmdir`, verify the same directory and emptiness.
   Never recursively remove a directory or accept substituted files just because their names match.
4. For each link, validate source provenance and destination absence; use a no-overwrite primitive,
   then check the exact two-name topology under lock. Validate the journal, content digest, operation
   ID, registration state, and all historical recovery states in the application. Perform required
   file/parent `fsync` operations in the application's reviewed order. An exception after publication
   is an uncertain outcome, even if cleanup did not finish.
5. Observe the grant's declared names/operations and lifetime throughout execution. The host runtime
   bounds process time by expiry, but cannot intercept undeclared I/O or an uncooperative descendant.
   Preserve recovery evidence on interruption; emit only selected nonsecret results to the receipt.

Harness signs observed parent/lock identities and existing scratch identities. Every command/path
preflight rechecks them and inspects declared existing scratch entries and pair topology using
metadata. It does not acquire the application's lock, inspect secret contents/digests, intercept
syscalls, or eliminate check/use races. The executor must repeat checks while holding the lock.
Do not claim a separate `policy-check` call is an atomic execution guard.

Pair states are `absent`, `source-only`, `paired`, and `destination-only`. A lone name must have link
count 1; the paired state requires equal device/inode and link count exactly 2 for both names.
Different inodes, an unknown second link, a third link, nonregular files, or symlinks fail closed.
These states describe filesystem relationships, not logical transaction correctness. A destination
which already exists cannot be linked over, even when it is the expected inode.

## Recovery

Keep the existing uncertainty marker after nonzero exit, timeout, or interruption. Do not rerun
issuance. First obtain a new command-bound read-only grant for the exact known names. For an existing
scratch directory, omit `mkdir/rmdir` and all child mutations. For pairs, keep only `stat/read`.
Metadata observations neither authorize deletion nor establish ownership of an old operation.

A new scratch name that unexpectedly exists is a collision. The original create grant cannot be
reused to inspect or clean that object. A separately approved inspection grant binds its current
identity. Unexpected child names block the scratch transaction; use a separate metadata-only
directory grant to inventory them without reading or deleting them.

After independent inspection, record fresh nonsecret reconciliation evidence using the existing
checkpoint protocol. Only then prepare a new, exact recovery command with a separate write grant.
Scratch recovery adds `created_identity` and `recovery_evidence`:

```json
{
  "name": ".probe-op42.scratch",
  "operations": ["stat", "list", "rmdir"],
  "created_identity": {"device": 2049, "inode": 12345, "owner": 1000, "mode": 448},
  "recovery_evidence": "evidence/op42-creation.json",
  "files": [{"name": "first", "operations": ["stat", "read", "delete"]}]
}
```

Values must come from the reviewed creation receipt, never invented or copied from a fresh `stat`
to manufacture ownership. `448` is decimal `0700`. Harness checks directory identity and that the
evidence reference is a project-local file; the agent reviews its origin and the executor validates
the receipt's full contents, child identities, and lock/transaction relation. This is an explicit
trust boundary, not cryptographic proof that the application wrote the receipt. If reliable creation
evidence is unavailable, authorize inspection only and leave cleanup blocked.

Valid historical two-link states may use the pair recovery contract. Unknown topology remains
blocked. Observation, relationship validation, or a grant cannot replace the application's journal,
digest, pending-registration, and rollback checks. Keep single-file keys outside pair declarations;
their existing multiple-link rejection remains in force. External stdout/stderr is discarded as
before. Receipts, purposes, file names, argv, and diagnostics must not carry secret values.

## Verification

Synthetic coverage lives in `tests/test_external_transactions.py` and
`tests/fixtures/transaction_executor.py`, alongside the existing grant lifecycle tests.

| Layer | Verified scope |
| --- | --- |
| Policy | Exact names/depth/operations, parent/lock identity, ownership/mode, pair topology, recovery separation, denied writes, CLI routing, signature compatibility and staged binding |
| Reviewed executor | Linux `renameat2` no-replace/exchange under an fd-anchored lock; exclusive creation, identity-based cleanup, `fsync`, two-name publication, probe/cleanup/link/post-publication faults, hard process exit with durable receipts, and separately granted recovery |
| Limits | Arbitrary subprocess I/O, malicious/unreviewed executors, races outside held locks, application-specific logic, native Mac primitives, Windows ACLs and live operational compatibility are not certified |

The synthetic format and its recovery do not establish compatibility with any production issuer.
Real operational files, keys, services, profile issuance, transfers, and platform installation tests
remain separate authorized work. Do not report all acceptance criteria as runtime-enforced merely
because policy tests pass.
