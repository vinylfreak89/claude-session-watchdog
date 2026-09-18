# Recover a failed recording through a verified retry

A receipt failure correctly stopped the gate, but no successful operation could
leave that state. The authorized recovery is successful recording of the same
operation, backed by actual delivery evidence. There is no release command.

The recording handlers call `recording_succeeded` only after their full delivery
validation and recording succeeds. Operation name, target-delivery UUID and item
ID (where applicable) must match the latched failure. Both standalone and
registered-payload `answered` routes count as the same `answered` operation;
owner-ack and prose-only item evidence do not. `sent1` and findings `sent` recover
only their own operations. Idempotent retries still run validation and save the
recovery even if the delivery was already recorded.

Before deleting the active latch, the code appends its complete original contents,
clear time, actor, operation, item ID and verified delivery UUID/timestamp to
`receipt_recording_recoveries`. The successful command reports recovery and
`owed` prints these episodes. A later refusal can latch again. Unreadable recovery
history keeps the gate stuck rather than discarding the history.

## Scope and limits

A different delivery or operation cannot prove recovery of the failed one. In
particular, a failure with no delivery UUID, a UUID that never existed, or
unreadable failure metadata remains stuck: there is no verified matching retry.
This change does not invent an administrative escape hatch for those cases.
The existing control that a different successful delivery does not erase the
first failure remains valid and passes.

Recovery clears only the recording latch. Receipt-health checks, possible
unrecorded deliveries, pending action, open target turns and holds still govern
the gate. It neither sends anything nor makes pending work complete.

## Controls

The handler controls added to `tests/test_acknowledged_turn.py` were run before
recovery existed, after the standalone acknowledgement repair:

```
FAIL: test_same_delivery_different_operation_does_not_clear (__main__.RecordingRecoveryContract)
FAIL: test_sent1_validated_retry_clears_its_own_failure (__main__.RecordingRecoveryContract)
FAIL: test_validated_retry_clears_and_archives_failure_then_can_latch_again (__main__.RecordingRecoveryContract)
Ran 16 tests in 0.100s
FAILED (failures=3)
```

Each failed because `receipt_recording_failure` was still present after a fully
validated retry. All seventeen final controls pass, including a findings `sent`
retry. They check initial STUCK, verified recovery, retained original failure,
re-latching after recovery, and refusal to clear on elapsed time, an owner-ack,
another delivery or another operation using the same delivery. The last case
then retries the actual failed operation and requires recovery, so it cannot
pass against an always-stuck implementation.

Scratch mutations confirmed both directions. Making recovery a no-op caused
four control failures. Removing operation/item/delivery matching caused two
errors in the unrelated-success controls, both `KeyError:
'receipt_recording_failure'`: the mutation incorrectly removed the latch those
controls require to survive.

Both full-suite runs pass without exclusions:

```
/usr/bin/python3 tests/run_all.py
RESULT: 31/31 scripts passed; 0 failed; exclusions: 0
python3 tests/run_all.py
RESULT: 31/31 scripts passed; 0 failed; exclusions: 0
```

## Read-only worked example

Using frozen target/watchdog transcript records and temporary copied state, the
real handlers produced this result for the supplied acknowledgement of the turn
ending `2026-09-18T10:17:56.327Z`:

| State | Before | After validated acknowledgement |
|---|---|---|
| Owed completed turns | 1 | 0 |
| Active recording failure | Present | Absent |
| Retained recovery episodes | 0 | 1 |
| Gate | STUCK: refused `answered` payload binding | TARGET BUSY (turn open), 2 queued |

The original failure said:

```
delivery does not uniquely match one queued item and/or complete finding messages
```

The gate's final output was:

```
TARGET BUSY (turn open). 2 queued. SEND NOTHING.
```

That is ordinary pacing, not the recording latch. Live state and configuration
were checked byte-for-byte afterward and were unchanged. No live queue item was
edited and no acknowledgement was written to live state.
