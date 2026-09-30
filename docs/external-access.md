# Narrow external operational access

## Why this exception exists

A production workflow may need selected configuration, signing material, a registration registry,
and a delivery file outside the source repository. Globally widening `read_paths`, opening a home
directory, or redefining the project root would give unrelated work the same authority. Instead,
HexaHarness records a separately approved scope for one task. Existing projects stay closed by
default and need no configuration migration.

The grant is not operating-system permission. Review the actual command implementation and respect
the host sandbox. Do not use this feature to work around a denied host permission.

## Phase 1: discover without issuing

1. Ask for the specific directory's `stat`/`list` operations, if inventory is needed. Listing grants
   do not authorize reading child contents or recursive traversal.
2. Select configuration files and the fields needed for the task. Obtain a read-only grant for
   those exact files. Read raw configuration with a reviewed inspector; do not invoke a production
   loader that also opens referenced keys, tokens, or stores before those paths are approved.
3. Add only confirmed transitive dependencies to a new approval. Never expose secret file contents
   in host read-tool output or chat. A reviewed program may use them internally and write a small
   report containing only approved, nonsecret fields to the project.

The agent prepares a project-local JSON scope and records already-given authorization:

```text
<HEXA> grant-external <task> --scope inspection-scope.json --approved -- <exact inspection argv>
<HEXA> policy-check --task-id <task> --access-grant <grant> --path <file> --operation read
<HEXA> run <task> --access-grant <grant> --approved -- <same exact inspection argv>
```

A metadata/read-only scope may omit argv for policy queries; it then cannot run a command.
Command grants require the complete argv and a declaration for every explicit external path
operand. `--config=/path` and split `--config /path` forms are supported. Paths discovered inside
configuration are not automatically approved.

## Phase 2: approve the complete issuance transaction

Inspect the issuer's file operations before asking. Identify the existing registry, a new profile
filename, and exact lock/journal names and temporary filename families. Atomic replacement and
cleanup need their own operations; create-only permission cannot replace an existing file.
External writes require a command-bound grant. An allowed path query does not authorize an ad hoc
host edit instead of that command.

Example scope (illustrative paths; replace expiry with an aware timestamp in the next 24 hours,
preferably just long enough for this operation):

```json
{
  "purpose": "Issue one approved enrollment profile",
  "expires_at": "2026-09-30T04:00:00Z",
  "paths": [
    {"path": "/srv/coach/config.json", "operations": ["stat", "read"]},
    {"path": "/srv/coach/signing.key", "operations": ["read"]},
    {"path": "/srv/coach/registration.json", "operations": ["stat", "read", "replace"]},
    {"path": "/srv/coach/profile-person.json", "operations": ["stat", "create", "read"]},
    {
      "path": "/srv/coach",
      "children": ["registration.lock", "registration.journal", "registration-stage-*.tmp"],
      "operations": ["stat", "read", "create", "replace", "delete"]
    }
  ],
  "arguments": [
    {"path": "/srv/coach/config.json", "operation": "read"},
    {"path": "/srv/coach", "operation": "transaction"}
  ]
}
```

Each rule names one absolute file, or an existing directory plus a nonrecursive child-name list.
At most one wildcard is allowed in a child name, after at least four fixed characters. It is for
specific temporary filename families, not `*`, `*.json`, or subtrees. File targets require an
existing parent. Use native canonical paths: symlinks, traversal, special files, multiply linked
files, project/home/system roots, and protected vault/chat/Git/SSH paths are rejected.
Existing denied-write rules still take precedence.

For exact depth-one scratch directories or two-name hardlink transactions, use the separate
[anchored transaction contract](external-transactions.md). These opt-ins add `transactions` to
the scope; they do not widen legacy `paths` or `children`. Review the executor contract before
approval, and pre-bind all generated names. Undeclared subprocess I/O is never implicitly approved.

Temporary families mean matching names **minus** protected and denied targets. Command preflight
checks exact writable names and existing family matches, including their metadata. The reviewed
scope includes internal, nonrecursive directory enumeration for this metadata validation; it does
not authorize exposing unrelated filenames or reading their contents. The reviewed
issuer must honor the same exclusions for newly generated names; transaction-anchor approval
cannot certify future subprocess I/O. Use concrete `policy-check` operations for such names when
the host controls their creation. Do not use a family to bypass a denied file.

The `arguments` list declares the operation of each external path operand in the exact approved
command. `transaction` permits passing a directory anchor with enumerated writable children; it
does not grant general directory modification. HexaHarness does not infer a tool's behavior from
an option name. The reviewed tool must obey the declared operations, including indirect accesses.

```text
<HEXA> grant-external <task> --scope issuance-scope.json --approved -- <exact issuer argv>
<HEXA> policy-check --task-id <task> --access-grant <grant> -- <exact issuer argv>
<HEXA> prepare-external <task> --access-grant <grant> -- <exact issuer argv>
<HEXA> run <task> --access-grant <grant> --approved -- <exact issuer argv>
```

Use the same grant throughout. The pending action binds its signed scope as well as argv, so a
different grant cannot be substituted after staging. Mutation commands run once, with no automatic
retry. The grant lifetime is independent of a profile's 24-hour validity: the issuer still needs
its own validity parameter, server-side pending state, and atomicity/recovery checks.
Read-only file grants do not downgrade commands already classified as publication or service
mutations: those still require the existing staging and result-verification protocol.

## Phase 3: verify or reconcile, then package

- On success, independently verify both the profile and server-side pending registration. Capture
  nonsecret evidence after the verification phase starts, then attach it with `checkpoint
  --completed <verification> --artifact <receipt> --clear-next`.
- On failure, timeout, stop, or interruption, preserve the reconciliation marker. Inspect actual
  state using an appropriately scoped read-only grant. If running an inspector after an escalation,
  explicitly reactivate the task; the original marker and consumed budget remain. Record fresh
  evidence with `--resolve-external-action` before considering a new issuance attempt. Do not
  delete journals/locks just to make the next attempt pass.
- Packaging uses a separate exact command and scope: read the one profile and create the named ZIP
  in the designated existing output directory. Do not include unrelated keys, registry files,
  personal conversations, or `owner.vault`. Record the expiry and delivery artifact hash without
  the enrollment token.
- Revoke unused authority with `revoke-external <task> <grant>`. Expiry, revocation, task termination,
  or the emergency stop blocks subsequent use. Revocation alone does not interrupt an active
  subprocess; use the emergency stop.

## Evidence and enforcement limits

All grant-based executions discard stdout/stderr before capture. Their logs retain redacted argv,
grant references in policy events, exit status, duration, timeout/stop flags, and lifecycle
transitions. Write safe selected results explicitly to project-local receipts. Do not put secrets
in argv, scope manifests, purpose descriptions, or those receipts. Path checks only use metadata;
they do not read or hash external secrets.

These are cooperative preflight controls, not an OS sandbox. They cannot intercept a subprocess's
hidden file access, prevent every check/use race, enforce a JSON field allowlist, freeze executable
source, or repair an issuer's transaction logic. Keep tool review and host permissions in place.
Grant creation does not itself issue a profile, prove Mac/Relay/GPT compatibility, or validate a
Windows install. Service changes, connection probes, deployment, and delivery remain separate
actions with their own authorization and evidence.
