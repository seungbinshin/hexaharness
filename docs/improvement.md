# Built-in improvement strategy (preview)

This is part of the existing HexaHarness package, CLI, configuration and primary skill.
The host can choose this strategy after repeated failure, unmet validation, or an explicit
improvement request. It is opt-in through `harness.yaml`; enabling it does not dispatch a model.

The first supported domain is a UTF-8 `.txt` or `.md` **data artifact**, at most 64 KiB.
The independent `literal-text-v1` evaluator checks case-sensitive literal presence/absence,
with weighted scores and mandatory checks. It never executes or interprets candidate text.
This verifies exactly the configured checks, not semantic quality, model performance, code
safety, or production effectiveness. Executable-code optimization and external evaluators are
not implemented. Unsupported evaluator types are rejected. The [host adapter](host-adapter.md)
binds model requests and receipts; actual provider execution remains the host's responsibility.

Add to the project's existing `.hexaharness/harness.yaml`:

```yaml
improvement:
  target: src/prompt.txt
  evaluator: literal-text-v1
  max_candidates: 5
  min_gain: 1
  checks:
    - name: preserve-caution
      text: carefully
      required: true
    - name: sources
      text: cite sources
      weight: 2
    - name: unsafe-instruction
      text: ignore all safeguards
      present: false
      required: true
```

The target must already be explicitly allowed for reading and writing. Policy files, skills,
tests, hidden paths, symlinks and executable extensions are not targets. A text extension is
not a sandbox: the host must select data, and existing sensors are trusted project commands.

## One task and one protocol

The primary skill uses `hexa improve <task-id> --action ...` internally. Every response has
`schema_version: 1`, `task_id`, `action` and `result`. Python hosts may call the typed functions
in `hexaharness.improvement` and use models in `hexaharness.improvement_models`.

1. `begin` freezes baseline bytes, configuration, guidance and evaluator identity in the
   existing task checkpoint. Repeating begin never replaces the baseline or resets budgets.
2. `request --key draft-1 --max-tokens 1000 --max-cost-usd 0.1` reserves the full host-call
   allowance in that task **before** dispatch. Repeated keys return `created: false`; never
   dispatch again on that response. Pass `--parent <id>` repeatedly to request a composition
   of existing candidates. The host creates or combines text; no model SDK is embedded here.
3. `submit --key draft-1 --candidate-file candidates/draft.txt --tokens 800 --cost-usd 0.08`
   reports actual usage and supplies text. Candidate files must be allowed local inputs.
   Omit unknown usage rather than inventing zero. Unknown/pending usage blocks further trials
   and promotion. Re-submit identical text with measured usage to reconcile an unknown result.
   Settled usage/output cannot be rewritten. Over-reservation usage is charged and blocks
   continuation; it must not be hidden by starting a replacement experiment.
4. `evaluate --candidate-id baseline --key baseline-1`, then evaluate a candidate with a new
   key. Each attempt charges the same task's tool/attempt budget. Output is bound to that
   attempt, candidate hash, evaluator hash and retained output hash. A new attempt invalidates
   the old score before it runs. Interrupted/failed attempts never inherit an earlier success.
5. `promote --candidate-id <id>` requires a positive gain meeting `min_gain`, mandatory checks,
   unchanged baseline target, fresh comparison evidence and passing existing required sensors.
   The local replacement is staged durably, then validated. Failure restores the baseline only
   if the target is still the exact candidate; concurrent user changes cause a conflict instead.
6. Normal `complete` remains the final project contract. Unresolved experiments and unknown
   usage block completion. No external publication or additional permission is implied.

Reservations are conservative: unused allowance is not refunded. They include host reasoning,
candidate generation and composition overhead. The host must enforce the supplied allowance
with its provider; this runtime cannot cancel a remote call it did not dispatch. Zero is valid
only for a measured free/manual operation, including deterministic test fixtures.

## Cancellation, recovery and compatibility

`cancel` durably signals without waiting for the task lock; running local sensor processes
observe it through the existing executor. Review/reactivate the original task if paused, then
`resume` reconciles running evaluations and rolls back an interrupted local promotion. It does
not rerun a host call, reset budgets or revive a terminal task. `resume --abandon` closes the
experiment without adoption; unknown usage must still be reported before task completion.

State is atomic within the existing TaskState file. The new optional `improvement` field uses
schema 1; legacy tasks/configurations without it remain readable, and future experiment
versions are rejected without rewriting. Existing signed external-action fields are untouched.
Evidence lives in the existing task artifact directory and follows its retention policy: active
tasks retain it; old terminal tasks may be pruned together with their checkpoints. Events are
audit references, not the sole source of recovery truth. No daemon, second plugin, independent
skill, approval store, work scheduler, or model retry loop is introduced.

## Release evidence still needed

The local deterministic examples prove bounded control flow and evidence integrity. They do
not establish useful real-model improvement. A general code-candidate version needs a measured
host adapter, enforced execution isolation, evaluator independence attestation, external-call
reconciliation and held-out quality trials before automatic adoption can be enabled for it.
Windows process-tree cancellation and final-artifact installation need direct Windows evidence;
passing launcher/bootstrap tests alone are not equivalent to those checks.
