# Stop sending when the receipt record cannot be trusted

The owner's ruling authorizes this constraint change:

> if I don't have a chance to respond directly, I would rather the machine as is gets stuck rather than continues to exist broken

`wd_check.next_item` owns the send decision, including a `stuck` verdict.
`next`, `due`, the wake report and the wait hook consume that decision; the wake
report no longer independently labels queued items sendable or suggests the
retired `queue clear` command. A stuck decision names the evidence problem,
says SEND NOTHING, and nominates no item. `next` and `due` exit nonzero for it.
The existing `due` checks for outstanding findings, running/in-flight work and
30 seconds of quiet are retained in that shared gate; consolidating the readers
does not remove those constraints. Two additional handler controls caught that
weakening in an intermediate version and now pass.

A refused delivery-recording attempt through `sent1`, `answered` or findings
`sent` retains the first operation, item/message identifiers when available,
time and reason in `receipt_recording_failure`. Receipt and obligation fields
remain unchanged. The diagnostic is not cleared by a later successful call,
owner acknowledgement, queue edits, restart or urgency. There is no reset,
force or bypass command. A malformed diagnostic also stops the gate. This can
leave legitimate subsequent sends impossible until human review; that is the
requested disposition, not an invitation to bypass the instrument.

The gate also reads the target transcript before nomination. An unreadable
record, or a peer population for which no delivery envelope validates, is
unavailable evidence. The format-health scan does not substitute the observed
transport identity for configured-sender attribution when recording receipts.
No observed peer traffic alone is not a failure: a first, genuinely unsent item
can still be nominated. This scan cannot predict a future filesystem write
failure or certify every transport shape from past traffic.

A queued item/finding's exact text occurring in a later peer delivery without
its send marker stops nomination as a **possible unrecorded delivery**, even
if that envelope itself cannot validate. This is deliberately only a reason
to stop: quotations or overlapping text can also cause it. It does not claim
semantic fulfillment, manufacture a receipt, or infer a declaration binding.
It covers a previously refused recording whose diagnostic predates this repair.
A read-only evaluation of the live gate returned `stuck`, with no nominee, for
the reported unrecorded item; the state file's bytes were unchanged.

There is no migration, backfill or manual credit for the four abandoned sends.
All live state, configuration and queue entries were left untouched.

## Controls and validation

The actual `next` and `due` handlers are tested for refused `sent1`, unavailable
validation, a delivered-but-unrecorded item, a recorded item and a genuinely
unsent item. Further controls cover urgent items, persistence after a later
success, malformed diagnostics/transcripts, multiline malformed envelopes, and
the wake report's use of the same gate.

Against `4545a09`, these three cases all failed:

- `test_refused_receipt_stops_next_and_due`
- `test_unavailable_validation_stops_next_and_due`
- `test_delivered_unrecorded_item_stops_without_credit_or_backfill`

Each produced this incorrect nomination (verbatim synthetic fixture output):

```
AssertionError: 0 == 0 : SEND EXACTLY THIS ONE ITEM, then run:  wd.sh sent1 Q1 <target-delivery-uuid>
---
Create artifact
---
0 other item(s) stay queued.
```

They now pass. Refusal controls that previously required the entire state to
remain identical now allow only the required failure diagnostic; all receipt
and obligation fields must still remain identical. They do not permit a
refusal to advance send timestamps or grant credit.

The whole suite, with no exclusions, passes on Python 3.9.6 and 3.14.7:

```
RESULT: 29/29 scripts passed; 0 failed; exclusions: 0
```

The nine declaration reproductions remain unchanged as the review record. Their
rejected schema proposal is not implemented by these receipt/gate repairs.

An extra receipt-only validation initially used a plain `git archive` tree.
Reconciliation stage 3 probes the watchdog repository itself (`wd_reconcile.py`),
so that non-repository test location could not finish stage 3. Its failure was:

```
RESULT: 3 FAILED: CRASHED test_cli_readings: KeyError('stage3'), CRASHED test_reaches_a_hundred: KeyError('stage3'), CRASHED test_cli_all_stages: KeyError('stage3')
RESULT: 28/29 scripts passed; 1 failed; exclusions: 0
```

Repeating the same staged receipt-only tree in a local Git clone passed 29/29.
No test was changed or excluded to fix that validation setup.
