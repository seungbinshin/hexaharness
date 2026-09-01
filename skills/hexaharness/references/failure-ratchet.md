# Failure ratchet

Read this reference after an observed failure or repeated review correction.

## Procedure

1. Reproduce the exact failure when doing so is safe and reversible.
2. Classify the root failure, not its surface symptom.
3. Choose the strongest practical layer.
4. Implement or record the correction.
5. Verify it on the original failing case.
6. Run the regression suite.

| Failure class | Preferred layer | Strong correction |
|---|---|---|
| `known-bad-pattern` | Sensor | Linter, schema, or regression test |
| `missing-context` | Guide | Precise, dated instruction with verification |
| `wrong-tool` | Permission | Narrow allowlist or deny boundary |
| `quality-drift` | Sensor | Acceptance test or calibrated advisory review |
| `state-loss` | Memory | Atomic checkpoint and recovery test |
| `unsafe-action` | Permission | Deny rule, approval gate, or environment constraint |
| `cost-overrun` | Observability | Finite budget and aggregate trip wire |

Use `<HEXA> learn --task-id <task-id> --evidence <artifact> --class ... --summary ... --fix ...
--verification ...`. Non-guide corrections are stored as proposals because changing sensors or
policy requires review. A guide correction can be applied with `--layer guide --guide-rule "..."`;
it is dated and linked to the source task and retained failure evidence.

Do not use a guide reminder when a deterministic sensor or permission boundary can prevent the
entire failure class. Do not add a universal rule from a one-off preference or speculative edge case.
