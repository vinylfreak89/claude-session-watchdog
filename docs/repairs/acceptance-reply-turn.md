# Credit the turn whose reply satisfied acceptance

The scope ruling resolves the remaining gate work: a closed message item's
exemption belongs to the completed target turn containing the reply call that
satisfied its acceptance. It belongs neither to the item delivery's preceding
turn nor to whichever turn happens to be newest.

`wd_acceptance.evaluate_message` now returns the qualifying turn alongside its
PASS result. It uses the same successful calls, exact contained line, full-body
correspondence, recipient and actual receiver evidence as the acceptance itself.
`unique_reply_turn` locates the call by tool-use ID AND timestamp. It grants no
turn attribution if candidate calls belong to different turns, the call's turn
is still open or not normally completed, or its end timestamp is ambiguous.
Historical uses of the same tool ID at other timestamps are not matches.

The additional result field is transient. No exemption marker, turn binding or
new state store is persisted. `accepted_reply_turns` reevaluates archived message
items against the real record when `owed` reads them, checking the original
registered item delivery too. A missing reply or unavailable acceptance cannot
supply an exemption merely because the item was previously archived.

This applies to an item archived by a mechanical PASS or by an attributed
`answered --evidence` report. The report itself grants no turn exemption: the
message acceptance must actually pass and locate its reply turn. Commit, file,
grep, CSV, row and task acceptances grant **no new turn credit or relay
exemption**, even when their action checks pass. The existing pre-delivery
receipt answer dispositions and owner-ack exception remain unchanged; they are
not sources of the new relay exemption.

## One gate, with the relay still visible

For the qualifying reply turn, `owed` recognizes the response as answered but
continues printing any outstanding `not relayed` duty. That row explicitly says
its relay is exempt from the send gate. The shared `next_item` function filters
only those verified rows out of its blocking unrelayed set. Other unrelayed
turns still block. A successful nomination names the retained relay duty rather
than claiming it was performed.

The queued item's own hold, active work, pending findings and receipt-health
refusals remain in force. No relay watermark is advanced and reading the gate
writes no state. Supersession alone, a hold disposition and a global owner-ack
are not the new exemption.

All callers need the configured watchdog identity to evaluate a message
acceptance. `next`, `due`, wake's digest and the wait hook now pass that identity
to the same gate. The `wd.sh due` wrapper uses its existing `WAKE_ARGS`, including
`--self`, rather than dropping that argument. With no configured identity, no
reply exemption is granted.

## Controls and falsifications

The eight cases in `tests/test_reply_turn_gate.py` use real queue, receipt,
acceptance, `answered`, `owed`, `next` and `due` handlers. The positive control
also checks the wait hook's gate output. The commit control performs an actual
push to a synthetic local bare repository and requires actual acceptance PASS
before testing that its unrelayed turn still blocks.

At the pre-repair implementation, three positive-path controls failed:

```
FAIL: test_neither_previous_nor_later_unrelayed_turn_is_exempt (__main__.ReplyTurnGate)
FAIL: test_operator_report_still_needs_actual_matching_reply_for_exemption (__main__.ReplyTurnGate)
FAIL: test_verified_reply_turn_is_answered_but_relay_stays_visible (__main__.ReplyTurnGate)
Ran 8 tests in 0.472s
FAILED (failures=3)
```

The first two produced exactly:

```
AssertionError: False != True : ITS LAST REPLY IS UNRELAYED (1 turn(s), oldest 2026-09-16T10:00:22Z). 1 queued. SEND NOTHING -- read it and relay to the owner first.
```

All eight now pass. The outside-turn test first requires nomination with only T
unrelayed, then separately adds debt for the pre-delivery turn and a later
unrelated turn; both must block. It does not merely check a negative case that
would pass if the gate always refused. The receipt-removal test first requires
nomination, then removes the matching receiver record and requires refusal.
Other controls cover a still-unanswered item, non-message prose evidence,
applicable holds and ambiguous candidate reply calls.

A scratch mutation replaced the scoped reply-turn set with every completed
turn whenever any archived item existed. The same controls rejected it:

```
FAIL: test_ambiguous_reply_calls_do_not_exempt_either_turn (__main__.ReplyTurnGate)
FAIL: test_commit_acceptance_grants_no_turn_exemption (__main__.ReplyTurnGate)
FAIL: test_neither_previous_nor_later_unrelayed_turn_is_exempt (__main__.ReplyTurnGate)
FAIL: test_nonmessage_prose_answer_grants_no_turn_exemption (__main__.ReplyTurnGate)
FAIL: test_operator_report_still_needs_actual_matching_reply_for_exemption (__main__.ReplyTurnGate)
Ran 8 tests in 0.375s
FAILED (failures=5)
```

Both complete suite runs exited zero:

```
/usr/bin/python3 tests/run_all.py  # Python 3.9.6
RESULT: 30/30 scripts passed; 0 failed; exclusions: 0
python3 tests/run_all.py           # Python 3.14.7
RESULT: 30/30 scripts passed; 0 failed; exclusions: 0
```

## Read-only real-record result

With the actual target and watchdog transcripts frozen in memory, the engine-test
item's acceptance returns PASS and attributes its reply to exactly:

```
2026-09-17T17:24:53.965Z
```

The item was already archived in the snapshot; no new item was closed even in
the in-memory poll. Its actual reply turn now receives answer credit. Six older
turns remain owed, all answer-only and none relay-exempt:

- 2026-09-16T13:24:38.668Z
- 2026-09-16T13:50:19.636Z
- 2026-09-16T14:01:34.863Z
- 2026-09-16T14:13:00.288Z
- 2026-09-16T14:17:14.619Z
- 2026-09-16T14:38:37.399Z

The geometry acknowledgement remains among them. No detector was loosened to
remove these turns. The census used copied state and checked state/config bytes
before and afterward; they were unchanged. No live state-writing poll, queue
edit, reconstructed receipt or push was performed.
