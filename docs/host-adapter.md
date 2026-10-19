# Experimental host model request and receipt contract

This protocol is experimental and requires opt-in improvement configuration. It is not a
stable-release promise of provider dispatch, remote cancellation or quality gains.

The built-in host adapter is a data protocol inside the existing improvement strategy. It
does not embed a provider SDK, launch a second agent, or grant execution/network permissions.
The existing host uses its approved provider connection and remains the single dispatch owner.

Before `host-request`, the host supplies a `HostAdapterSpec` with adapter/model identity and
confirmed token/cost ceilings, cancellation and result reconciliation support. All four flags
default to false. They are host assertions, not independent proof about a provider. Unknown
capabilities block before reservation. Do not set a flag merely to obtain a request envelope.

The internal CLI is `hexa improve TASK --action host-request --key KEY --adapter-file FILE
--goal TEXT --max-tokens N --max-cost-usd N --timeout-seconds N`. Adapter/receipt files are
allowed regular project inputs, bounded to 64 KiB; the host can also use typed Python APIs.

The response has `created` and a versioned `request` containing an opaque call ID, intent
digest, adapter/model IDs, goal, parent candidate texts, exact reservations, one-call limit,
tools-disabled requirement and absolute deadline. Evaluator checks, policy, local paths and
credentials are not included. Supply a public development objective in `goal`; do not reveal
held-out criteria through it. On `created: false`, inspect/reconcile the original call and
never send it again. Reusing a key for another intent is rejected.

The host must enforce the envelope through its actual provider and native controls. If it
cannot enforce a requirement, leave the call undispatched and preserve the reservation.
The deadline starts before reservation persistence, so slow storage cannot extend the call's
allowance. A returned envelope can already be expired; the host must never dispatch it after
its deadline. Its identity and reservation remain available for inspection and reconciliation.
There is no runtime claim that a JSON envelope itself isolates a process or limits a remote bill.

Submit `HostResult` through `--action host-result --key KEY --receipt-file FILE`. A receipt binds
the exact call, request digest, adapter/model, provider request identity, outcome, output and
usage. `provenance` is `provider` or `fixture`; fixture results never become real-model evidence.
This label is host-reported provenance, not provider authentication. Persist the original
provider evidence in the host's approved evidence store; do not copy credentials into a receipt.
Request identity, parent text, reservations and the retained receipt digest are checked again
before fresh evaluation, adoption and completion. A later partial change to provider identity,
usage or receipt evidence invalidates the experiment. Digests detect changed evidence; they do
not authenticate a provider or protect against a host that can rewrite all trusted state.

Unknown token/cost values remain null. They block evaluation and adoption, and cannot be
replaced with zero. Reconcile the same output/identity later with measurements; already known
usage cannot be reduced or rewritten. Overruns are charged immediately and stop further trials.
Failed/cancelled receipts with measured usage settle a call without inventing a candidate.
An unknown outcome keeps it unresolved even when some usage was observed.

`host-cancel` durably signals local cancellation and returns the exact remote call identity.
The host must invoke its provider cancellation operation and submit the resulting receipt.
Requesting cancellation does not mean it succeeded remotely and does not refund reservations.
Late receipts can reconcile stopped work without restarting the model or enabling promotion.

Tests exercise the real CLI and checkpoint protocol with labelled deterministic receipts.
They establish ledger, identity and failure behavior. Actual provider execution, remote
cancellation and useful held-out model-quality improvement need separate execution evidence.
