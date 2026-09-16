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
