# Model calibration

HexaHarness gives two kinds of instruction to the host agent. This document tells an operator how
to select the second kind for each host model.

| Kind | Content | Depends on the host model |
|---|---|---|
| Controls | Policy decisions, bounded execution, write observations, checkpoints, the approval boundary, `complete` and its required sensors | No |
| Process modules | How to plan, repair, verify, and review | Yes |

A control prevents a failure class with every model. A process module compensates for something
that the host model does not do without instruction. The same instruction helps one model and costs
another: the vendor guidance for one current model tells operators to remove verification
instructions, and the guidance for a smaller model of the same generation tells operators to add
them. The effort setting changes the result for one model. A model name does not predict it.

## Profiles

`HEXAHARNESS_PROFILE` selects the process modules for a host. The skill resolves it with
`skills/hexaharness/scripts/process_profile.py`, which reads this variable only.

| Value | Modules | Use |
|---|---|---|
| unset, or `guided` | `plan`, `repair`, `verify`, `review` | Default. The process guidance of 1.0. Use it for each host model that you did not measure. |
| a list, for example `verify,review` | The listed modules | A reduction that you measured for this host. |
| `lean` | None | A host model that plans, verifies, and corrects its work without instruction. |

| Module | Instruction | Assumption about the host model |
|---|---|---|
| `plan` | State acceptance criteria. Take the smallest useful next step. Define the smallest release slice. Divide work into milestones. | It loses direction or scope in long work. |
| `repair` | Classify the cause of a failure before acting. | It repeats a failed action without diagnosis. |
| `verify` | Map each criterion to evidence. Run sensors and task checks before completion. Inspect the final diff. | It reports work as complete without a check. |
| `review` | Assign a reviewer that did not produce the artifact. | It rates its own work too high. |

An unrecognized value resolves to every module. The script does not read the model name, and the
skill tells the host agent not to select modules from its own judgment. A wrong `lean` on a weak
model causes failed work. A wrong `guided` on a strong model causes extra tokens. Thus the default
is `guided`.

`lean` has one more effect. Single-session supervised work in a project that has no
`.hexaharness/harness.yaml` stays outside the runtime, and the skill does not run `init` for it.

## Set the profile for one host

Set the variable in the settings of the host, not in a login shell. A login shell gives the value
to each host that starts from it.

- Host with a measured frontier model, for example Claude Code: add `"HEXAHARNESS_PROFILE": "lean"`
  to the `env` object of the Claude Code `settings.json`.
- Host with a model that you did not measure, for example a proxied internal model in a Codex
  shell: do not set the variable. To make the selection explicit, set it to `guided` in the
  environment that starts that host.

To confirm the value that a host uses, ask the host agent to run `process_profile.py` and read
`source`: `default`, `environment`, or `invalid-environment`.

## Decide with evidence

Change a profile only after these steps. Do them again when the model or its effort setting changes.

1. **Baseline.** Keep `guided`. Complete ten or more real tasks, with three or more that need
   substantial design. Count the signals in the table below.
2. **Remove one module.** Set the list without one module, for example `plan,repair,review` to
   remove `verify`. Remove modules in this sequence: `verify`, `repair`, `plan`, `review`. The
   first two have deterministic signals. Complete the same number of tasks of the same types.
3. **Keep the removal** when the signal of the removed module is not more frequent than in the
   baseline and each task met its acceptance criteria. Then remove the next module.
4. **Restore the module** at the first failure that the removed module prevents. Record the failure
   with `learn` (see `references/failure-ratchet.md`). Prefer a sensor or a permission to a new
   instruction.

| Module | Signal that the module is necessary | Where to find it |
|---|---|---|
| `verify` | `complete` was tried while a required sensor failed | `trip-wire.fired` events with `"name":"sensor-regression"` |
| `repair` | One command failed three times in sequence | `trip-wire.fired` events with `"name":"repeated-error"` |
| `plan` | A limit stopped a task, or a task was stopped before completion | `budget.exhausted` events; tasks with status `escalated` or `stopped` |
| `review` | A defect found after `complete` | `learn` records with class `quality-drift` |

Events are in `.hexaharness/events/<UTC date>.jsonl`. For example:

```text
grep -h '"trip-wire.fired"' .hexaharness/events/*.jsonl | grep -c '"name":"sensor-regression"'
grep -hc '"event_type":"task.completed"' .hexaharness/events/*.jsonl
```

The runtime does not record the profile of a task. Use one profile for each trial period and write
down its dates. Controls stay active in each profile, so a trial cannot remove the approval
boundary, the limits, or the completion gate.

## Fixed cost of one task

Measured with plugin 1.0.0, CPython 3.13, Linux, a two-function repair in a three-test project.

| Item | Value |
|---|---|
| Control invocations that a `change` task needs | 5: `doctor`, `status`, `start`, one `policy-check`, `complete` |
| Invocations in the measured repair | 12: `init`, the 5 above, 4 wrapped test runs, 2 for trip-wire recovery |
| Runtime output returned to the host | 14.8 KB |
| Warm runtime invocation | 0.28 s |
| `process_profile.py` invocation | 0.02 s |

## Limits and reasoning effort

- Wall time is the time from task start to the present, minus approval waits. It includes the
  reasoning time of the host between commands. The default limit is 1,800 seconds.
- The other default limits are 50 tool calls, 100,000 host-reported tokens, and 5 USD.
- To change a limit, edit `budgets` in `harness.yaml` and the trip wire that has the same metric
  (`tool-budget`, `cost-budget`). `doctor` does not report a difference between the two. Measured:
  with `budgets.max_tool_calls: 200` and the `tool-budget` threshold at 50, the task escalated at
  call 51.
- Set limits again for each model and effort level.

## Runtime limits found during calibration

These are observations for maintainers. This change does not modify the runtime.

1. The repeated-error fingerprint is argv, exit code, and timeout state. It does not include the
   output. Three runs of one test command with a different repair before each run, and exit code 1
   each time, escalate the task. The skill prevents this with a narrow check after each repair. A
   fingerprint that includes a normalized digest of the failing output removes the cause.
2. `doctor` returns `ok` when a budget and its trip wire disagree.
3. The wall-time limit cannot separate command time from host reasoning time.
4. A task does not record its process profile, so evidence cannot be divided by profile without
   trial dates.

## Sources

- Anthropic, [Prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5):
  remove verification instructions; they cause over-verification.
- Anthropic, [Prompting Claude Haiku 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5):
  add a verification instruction; the model sometimes reports a change as done without a check.
- Anthropic, [Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5):
  effort calibration and unattended runs.
- Anthropic, [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps):
  each harness component is an assumption about a model limit.
