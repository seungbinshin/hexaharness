# Provider readiness assessment — 2026-10-19

The experimental host handoff is **not enabled by default** and the runtime has no provider
dispatcher. This assessment distinguishes an available login from a verified call contract.
No model request or provider mutation was performed for this assessment. Credential files and
secret values were not inspected; only command help/version and authentication status were used.

| Question | Observed evidence and conclusion |
|---|---|
| Connection absent? | The existing Codex CLI 0.159.2 reports ChatGPT authentication. A connection is configured; availability of a particular new call remains untested. The separate Claude CLI previously reported no authenticated connection. |
| Hard call/token/cost allowance? | `codex exec --help` has no per-request monetary cap or one-inference limit. Official docs describe post-turn token usage and experimental rollout token tracking. These do not establish the envelope's hard monetary ceiling or total inference count. No API key, billing change or purchase was introduced. |
| Cancellation and reconciliation? | Killing a local CLI process can stop local work but is not a provider cancellation acknowledgment or a way to recover the final usage of an interrupted remote call. The examined CLI contract does not establish either guarantee. |
| Existing runtime implementation? | `host_adapter.py` creates and reconciles data envelopes; it does not send requests. All four `HostAdapterSpec` capability flags default false and must be verified before a request is reserved. Fixtures verify the ledger, not the provider. |

Thus this is **authentication available, required per-call limits and remote cancellation/usage
reconciliation unverified**, rather than a missing login alone. It is not evidence that every
Codex integration lacks those capabilities, only that this examined route has not established them.
A bounded live probe cannot honestly declare the four adapter flags true on this evidence.

No additional approval is pending for stable runtime publication. A future optional probe should
use a host integration that supplies those capabilities and existing authorized access. A concrete
small trial would be one public-text generation, tools disabled, at most 2,000 total tokens, $0.10,
and 60 seconds, with no new credentials or account changes. These proposed limits are **not an
assertion that the current CLI enforces them**. If a new connection, billing scope or paid resource
is needed, obtain explicit approval for that exact provider/action/allowance before provisioning it.
Do not manufacture zero cost for subscription access or unknown usage after cancellation.

Stable command cancellation, task budget enforcement and receipts are tested independently.
Research claims about useful model improvement require held-out repeated trials; literal string
checks are not a substitute. Keeping this opt-in feature experimental does not waive its
identity, evidence, accounting or policy tests.

Official sources consulted:
- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode): JSON turn usage and existing authentication reuse.
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference): experimental rollout budget tracking.
