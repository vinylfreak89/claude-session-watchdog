# Record an actual standalone acknowledgement

The registered-payload route on `answered` cannot represent a reply that
acknowledges a target turn without carrying a queued item or finding. The skill
requires such replies. Refusing to record them leaves the response count wrong.

The authorized route is:

```
wd.sh answered <turn end_ts> --acknowledged <target delivery uuid> "<what was acknowledged>"
```

It resolves exactly one completed target turn and exactly one delivery record in
the target transcript. The existing `wd_receipts.delivery` validates sender,
envelope and actual delivered surface. Delivery must be strictly later than the
named turn. Nonempty explanatory prose, acting session, recording timestamp,
target, turn timestamp/fingerprint and delivery UUID/timestamp/body hash are
appended to `acknowledged_turns`. A delivery already recorded against another
turn cannot be spent again, including by switching between the standalone and
registered-payload routes. Both routes may record the same delivery for the same
turn. Further explanations append;
they never overwrite earlier entries.

`owed` revalidates the evidence and prints the retained reports. Invalid evidence
prints `INVALID ACKNOWLEDGEMENT` and earns no response credit. The prose is an
attributed human-reviewable claim about what was acknowledged, not a mechanical
proof that the message substantively answers a question. Question detection
therefore does not veto this route; it still vetoes hold and closed.

Credit is limited to the named turn's RESPOND duty. It neither grants a relay
exemption nor moves a global answer watermark, settles queued work, or reconstructs
an item receipt. The follow-up [receipt recovery repair](receipt-recording-recovery.md)
allows a successful retry to clear its own failure while retaining the episode. The retained
owner-ack exception and the existing delivery/evidence routes are unchanged.

## Controls and falsifications

`tests/test_acknowledged_turn.py` exercises real `answered`, `owed`, queue and
receipt handlers using synthetic transcripts and isolated state. The first nine
controls were run before the implementation against `a392f9b`:

```
FAIL: test_changed_delivery_invalidates_credit (__main__.AcknowledgementContract)
FAIL: test_exact_turn_only_and_relay_is_preserved (__main__.AcknowledgementContract)
FAIL: test_history_is_appended_and_delivery_cannot_answer_another_turn (__main__.AcknowledgementContract)
FAIL: test_real_later_reply_answers_question_without_queue_or_finding (__main__.AcknowledgementContract)
Ran 9 tests in 0.036s
FAILED (failures=4)
```

The positive route failed with:

```
AssertionError: 1 != 0 : REFUSED: answered <target delivery uuid>; the body must match registered obligations
```

The refusal controls already passed because the old handler refused the entire
unsupported command. They are regression controls, not evidence that the new
route worked. The positive controls prevent an always-refusing implementation
from passing. All twelve final cases pass, including additional checks that
delivered queue work stays pending and ambiguous deliveries/nonexistent turns
are refused. Cross-route reuse against a different turn is refused in either
order; reuse against the same turn remains valid.

Two scratch mutations demonstrate that persisting a report alone is insufficient:

```
no-credit: remove the acknowledgement credit from owed
FAIL: test_exact_turn_only_and_relay_is_preserved (__main__.AcknowledgementContract)
FAIL: test_real_later_reply_answers_question_without_queue_or_finding (__main__.AcknowledgementContract)
FAILED (failures=2)

all-turns: give every turn credit when any acknowledgement exists
FAIL: test_exact_turn_only_and_relay_is_preserved (__main__.AcknowledgementContract)
FAILED (failures=1)
```

Both full-suite interpreters passed with no exclusions:

```
/usr/bin/python3 tests/run_all.py
RESULT: 31/31 scripts passed; 0 failed; exclusions: 0
python3 tests/run_all.py
RESULT: 31/31 scripts passed; 0 failed; exclusions: 0
```

## Real-record check without live writes

Target and watchdog transcripts were frozen in memory. The actual handlers ran
against a temporary copy of state using the reported target delivery UUID and
the completed turn at `2026-09-18T10:17:56.327Z`:

```
BEFORE: OWED completed turns: 1  DUE NUDGES: 0
   2026-09-18T10:17:56.327Z  [not answered or held]
ACK RC: 0
AFTER: OWED completed turns: 0  DUE NUDGES: 0
```

Live state and config bytes were checked before and afterward and were unchanged.
No live acknowledgement, queue edit or receipt reconstruction was performed.
