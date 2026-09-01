# Six-layer contract

Read this reference when initializing, redesigning, or explaining an audit finding.

| Layer | Required outcome | HexaHarness evidence |
|---|---|---|
| Guides | Exact commands, constraints, trusted sources, and failure-derived rules | `AGENTS.md`, `CLAUDE.md`, `.hexaharness/GUIDES.md` |
| Sensors | Every critical output has a computational check where possible | `harness.yaml` sensor arrays and retained output logs |
| Agentic loop | Plan, execute, verify, fix, and escalate within finite bounds | task checkpoint, attempt count, escalation packet |
| Memory | Work survives restart without repeating completed steps | `.hexaharness/state/<task>.json` and artifact paths |
| Permissions | Scope, rate, reversibility, and visibility are explicit | allow/ask/deny policy decisions and approval events |
| Observability | Actions, results, costs, approvals, and trip wires are traceable | `.hexaharness/events/*.jsonl` and `<HEXA> audit` |

## Production-readiness evidence

Before unattended use, require all of the following:

1. Guide with exact build, test, and lint commands.
2. At least five rules traceable to real failures.
3. A required computational sensor for each critical task output.
4. Bounded retry and structured escalation.
5. A filesystem checkpoint that survives restart.
6. An allow/ask/deny permission boundary.
7. Token and cost budgets, even when the host reports zero usage.
8. Structured event records.
9. Tested trip wires for repeated errors and aggregate budgets.
10. Explicit trusted/untrusted input separation.
11. An emergency stop that preserves current state.
12. Three successful unattended runs using real acceptance criteria.

Warnings for rules and unattended runs represent evidence still to accumulate. Do not fabricate
them, downgrade them silently, or substitute configuration for observed behavior.

## Growth rule

Start with one guide, the project's existing deterministic checks, JSON state, narrow permissions,
and local JSONL events. Add infrastructure only after a real failure proves the current layer
insufficient. Review guide rules periodically and retire rules once stronger sensors or environment
constraints make them redundant.
