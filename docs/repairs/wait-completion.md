# Wait-side completion and stalls

The wait monitor discarded `task_output_status().exit_code` and inferred stalls
from process absence alone. Unlike wake, it never removed completed tasks from
`in_flight`. Completion is now an explicit result of the probe. An exit marker,
including a nonzero code, prevents a stall and retires the exact probed item.
This records termination, not success. Missing markers still allow stalls.

Retirement reloads state under `wd_state.transaction`, preserves other writes,
and removes only an unchanged item. A replacement using the same task ID stays
tracked. Completed items also leave the monitor's persisted stall memory. The
stderr diagnostic names `FINISHED`, `exit_marker`, and the exit code; unfinished
background stall reasons name `missing_exit_marker`. No target file is changed.
As before, interrogation runs after `--stale-after`; setting it to zero disables
interrogation. The audit-only path continues to report state without probing it.

## Controls through `wd_wait.main`

Added to the existing `tests/test_monitor_lifecycle.py` script, using actual
output files, an elapsed 33-minute window, and zero live processes:

- `test_bg_exit_marker_finishes_and_evicts`: codes 0 and 1 produce no STALL,
  persist removal, and are probed once across successive polls.
- `test_bg_missing_exit_marker_still_stalls`: the same window without a marker
  produces STALL and retains the item.
- `test_bg_completion_clears_persisted_stall`: a previously reported signature
  cannot hide completion or leave obsolete stall memory behind.
- `test_completion_preserves_concurrent_state_update`: inject a state write
  during the probe; its replacement item and unrelated field survive eviction.

Before the fix, the first control reported this failure verbatim (synthetic
fixture only):

```
AssertionError: 0 != 3 : [wd_wait 2026-09-16T16:13:36Z] watching control (local_control_target) ct=1 cec=0 stale_after=1s stall_min=20.0 idle_after=0s
STALL kind=bg id=background-control idle_min=33 reason=output_38_bytes,_mtime_2026-09-16T10:00:20Z,_live_processes_0
NEXT: nothing queued.
USAGE: send nothing. Only the owner marking an item send-immediately overrides the gate.
```

The script reported `Ran 7 tests` / `FAILED (failures=5)` before the repair
(including both exit-code subtests), then `Ran 7 tests` / `OK` after it. The
positive missing-marker control already produced STALL before the fix; its
new reason-name assertion failed until the diagnostic was made explicit.

## The same distinction in Codex interrogation

The old `assessable and not in_flight` condition also suppressed stalls for a
rollout with no lifecycle events, or with completion predating the tracked
dispatch. Conversely, wait ignored an exit marker on a dispatch's output file
when its rollout was missing. Wake already checks that marker and dates rollout
completion after dispatch. Wait now does likewise, retires completed dispatches,
and retains unknown or unconfirmed completion. The rollout route requires
explicit `lifecycle_known=True`, `in_flight=False`, and parseable completion and
launch times with completion strictly later. Missing facts cannot retire work.

Three additional real-handler controls failed before this second repair:

- `test_old_codex_completion_does_not_finish_new_dispatch` reported
  `AssertionError: 3 != 0` and a HEARTBEAT instead of a STALL.
- `test_no_codex_lifecycle_is_not_completion` reported
  `AssertionError: 3 != 0` and a HEARTBEAT instead of a STALL.
- `test_codex_output_exit_marker_finishes_without_rollout` reported
  `AssertionError: 0 != 3` followed by
  `STALL kind=codex thread=background-control idle_min=33 reason=no_rollout_found_for_thread_00000000`.

The existing known-completed-rollout control gained a persisted-eviction
assertion, which also failed before this repair. Together the script reported
`Ran 10 tests` / `FAILED (failures=5)`, then `Ran 10 tests` / `OK` after it.
Unknown lifecycle still produces a stall after its window; absence of a process
or of a start event never establishes completion.

## Other wait-side lifecycle consumers examined

`idle_check` consults tracked work after interrogation, so removal now prevents
completed rows from suppressing IDLE indefinitely when interrogation is enabled.
The reply deadline path uses peer replies, not process presence.

`outstanding_agents` remains a separate heuristic: two user-record mentions of
an agent ID count as a return, and launch discovery examines six recent turns.
From code inspection, a quoted mention can therefore hide a pending agent, and
an older launch can leave the search window. These are **reasoned findings, not
reproduced controls in this repair**. A follow-up should use typed launch and
completion records with durable tracking; this change does not claim that the
subagent heuristic proves completion. No relaxation or new exception was added.

## Final validation

Both whole-suite commands exited zero, with no skipped script or known-failing
exception:

```
/usr/bin/python3 tests/run_all.py  # Python 3.9.6
RESULT: 29/29 scripts passed; 0 failed; exclusions: 0

python3 tests/run_all.py           # Python 3.14.7
RESULT: 29/29 scripts passed; 0 failed; exclusions: 0
```

An in-memory mutation replacing `wd_wait.main` with a successful no-op made both
background controls fail. The harness reported
`NO-OP MUTATION REJECTED: both background handler controls fail`. The controls
therefore require the real command's observable alarm and persisted retirement,
not just the output parser's exit-code fact.
