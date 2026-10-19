# Continuing explicitly authorized work

The default 1,800 seconds and 50 calls are project defaults, not a native sandbox or an
account limit. Exhaustion preserves the checkpoint. It does not authorize a replacement
task with empty counters. Native execution and account limits always remain authoritative.

When the user explicitly authorizes more work and a concrete finite allowance, the host
can use `hexa continue GOAL --source TASK --approved --reason REASON --deadline ISO_TIME
--max-wall-seconds N --max-tools N --max-tokens N --max-cost-usd N`. All limits and an
absolute timezone-aware deadline are required. `--source` can be repeated for disjoint
terminal tasks. This is an internal host operation; users need not manage IDs.

The assertion `--approved` records authorization already given for this scope and these
limits. It is not authentication and cannot grant host permissions. Do not infer permission
to increase cost, buy credits, change credentials, or remove an emergency stop.

The successor inherits all source call, attempt, token and cost counters and the earliest
start time. Overlapping ancestry, sources already continued, active sources, and unresolved
external actions are rejected. Approval wait from separate historical tasks is not refunded;
the absolute deadline includes future approval waits. Original checkpoints remain unchanged.
Commands, sensors, improvement reservations and completion use the same task limit.

The new immutable `task_budget` field is omitted entirely on legacy task JSON. It includes
limits, absolute deadline, authorization reason, source IDs, ancestry and checkpoint hashes.
It changes no project or installed-plugin settings. The canonical tripwires duplicating
the original default budget follow the approved task budget; custom or stricter independent
tripwires remain enforced. A later continuation also requires explicit authorization.

Grants, pending-action nonces, old sensor passes and old artifact claims are not transferred
to the new task. A successor must obtain evidence for its own outcome. The continuation
record is not a completed release or a statement that prior failures disappeared.

Token/cost counters reflect values reported or reserved by the host. Unmeasured conversation
usage must be identified separately; a zero stored counter does not establish free execution.
Do not dispatch a model when its call allowance or cost accounting cannot be established.
State retention applies to the source and successor checkpoints; keep them while the work
or its audit evidence is needed.
