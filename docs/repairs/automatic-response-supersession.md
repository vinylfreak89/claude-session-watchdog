# Automatic response supersession and recorded item evidence

The current owner ruling replaces the proposed manual span with automatic
supersession inside `owed`. No span argument or new verb was added.

An older completed turn with no `wd_turns.question_evidence` is answered by a
later completed turn. A flagged question remains owed; so does the newest
completed turn until it receives a disposition. An open later turn cannot
supersede the head. This changes RESPOND only: unrelayed turns remain listed
as `not relayed`, and `last_relay_ts` does not move.

The existing `answered` handler also accepts:

```
wd.sh answered <item id> --evidence "what I checked and what came back" --open "remaining work"
```

`--open` is optional. The original item text, original mechanical acceptance and
registered delivery remain unchanged in the existing item archive. Each report
appends to that item's `answers`, retaining actor, target, timestamp, evidence and
residue. Every `owed` poll prints those reports in full. No owner obligation is
created, no send timestamp advances, and no mechanical PASS is claimed. The
owner judges the prose; the machine does not parse or score it.

A missing registered receipt, undelivered or ambiguous item, empty evidence,
conflicting routes and repeated options refuse. Receipt validation happens on a
copy; no missing delivery is reconstructed. Malformed archived answer evidence
makes the common send gate STUCK rather than silently accepting the archive as
completed work. Existing receipt answering and owner-ack remain available.

## Remaining scope decision: item closure versus the reply head

The evidence route above closes the identified delivered item. It does **not**
claim an unrelated turn was answered. The requested relaxation of the unrelayed
turn gate has not been implemented pending clarification of that scope.

An existing item receipt's `turn_ts` identifies the completed turn **before**
delivery (`wd_receipts.receipt`), not the later close-out response. In a synthetic
record, delivery at 10:00:10 points to the prior turn ending at 10:00:01, while
the response ends at 10:00:22. Reusing that field for a later reply is wrong;
choosing the latest completed turn merely because it is latest can exempt an
unrelated question. Automatically superseding narration does not establish
that a retained question or head belongs to a particular item.

The operator-facing clarification asks which turn an item-specific evidence
answer should credit. No new identity contract, inferred work thread, default
span or global answer watermark was invented. The send gate continues to block
unrelayed turns while that interpretation is unresolved. This is an explicit
remaining part of the requested work, not a claim that the whole repair is done.

## Owner-ack scope

The current `--owner-ack` implementation intentionally records one global time.
Every completed turn through it loses its answer requirement. Relay obligations
remain, later turns remain unanswered and pending item acceptance is not
archived. Both the existing owner-ack control and
`docs/proposals/owner-ack-provenance.md` document that scope. It is not an
accidental new side effect of this repair. It is also not evidence that an
owner statement about one item authorizes a global acknowledgement. The owner
must decide any change to the exception; this repair leaves it unchanged.

## Real-record census without writes

Before the change, 23 turns had answer debt; none had relay debt. Applying only
automatic supersession leaves eight: seven older turns flagged by the existing
question reader, and the newest engine-test close-out.

The contained-line acceptance repair additionally lets the real engine-test item
reach PASS. Simulating the `owed` poll in memory, including its ordinary
`settle_acted` step, leaves **seven owed turns, all answer-only**:

| Turn end, UTC | Why it survives |
| --- | --- |
| 2026-09-16T13:24:38.668Z | Request-language detector |
| 2026-09-16T13:50:19.636Z | Question-punctuation detector |
| 2026-09-16T14:01:34.863Z | Request-language detector; the geometry acknowledgement |
| 2026-09-16T14:13:00.288Z | Request-language detector |
| 2026-09-16T14:17:14.619Z | Request-language detector |
| 2026-09-16T14:38:37.399Z | Request-language detector |
| 2026-09-17T17:24:53.965Z | Newest completed turn; no later completion supersedes it |

The eighth turn before the simulated acceptance poll is
2026-09-16T15:12:01.023Z. The normal verified-item receipt disposition answers it
when the engine-test item passes. It is the pre-delivery turn referenced by that
item's receipt, not an inferred association with the engine-test close-out.

This does not reach the forecast one or two turns. The request detector is
intentionally conservative and was not loosened to reach that forecast. On the
actual full geometry turn it reports `direct question or request language`.
The shortened sentence quoted in the steer, by itself, returns no question
evidence; it must not substitute for the complete record in a control.

All these evaluations used copied state. The actual state/config bytes were
checked before and afterward and were unchanged. No live acceptance poll or
queue mutation was performed.

## Controls and failures

Before the supersession implementation, the new real-handler controls reported:

```
FAIL: test_later_completed_turn_supersedes_narration_only (__main__.DispositionContract)
FAIL: test_supersession_preserves_relay_duty (__main__.DispositionContract)
Ran 12 tests in 0.064s
FAILED (failures=2)
```

Before the evidence handler, its initial seven cases reported:

```
FAIL: test_evidence_closes_only_delivered_item_and_retains_original (__main__.AnsweredEvidence)
FAIL: test_repeated_answer_appends_without_overwriting (__main__.AnsweredEvidence)
FAIL: test_reported_residue_is_visible_and_never_owner_obligation (__main__.AnsweredEvidence)
Ran 7 tests in 0.076s
FAILED (failures=3)
```

Negative controls already refused on the baseline, which has no evidence route;
they cannot honestly be claimed as red-then-green. The positive controls require
actual retained records and handler behavior, rather than accepting mere success
exit codes. Additional negatives exercise corrupted reports and conflicting
arguments. Manual-span controls were discarded when the owner superseded that
proposal; no span behavior exists in the mechanism.

Two existing controls initially failed because their earlier turns were plain
narration, now legitimately superseded:

```
FAIL: test_explicit_owner_ack_restores_turn_disposition (__main__.OwnerAckContract)
AssertionError: '2026-09-16T10:00:01Z' not found in 'OWED completed turns: 1  DUE NUDGES: 0\n   2026-09-16T10:00:03Z  [not answered or held]  Synthetic result\nWHAT EACH AGENT SAYS IT OWES: 0\nSENT, NOT YET ACTED ON: 0\nUNANSWERED requests: 0 (due to nudge: 0)\n'
FAIL: test_one_receipt_answers_only_one_prior_turn (__main__.ReceiptContract)
AssertionError: '2026-09-16T10:00:01Z' not found in 'OWED completed turns: 0  DUE NUDGES: 0\n   nothing owed\nWHAT EACH AGENT SAYS IT OWES: 0\nSENT, NOT YET ACTED ON: 0  (closed this poll: Q1)\nRECORDED ANSWER EVIDENCE (operator reports for owner judgement): 0\nUNANSWERED requests: 0 (due to nudge: 0)\n'
```

Their underlying requirements remain valid. Their fixtures now contain a real
question, so they still distinguish owner-ack's global scope from one receipt's
specific scope. Assertions were retained; neither exception was broadened.

Both full-suite commands now pass, including the new evidence script:

```
/usr/bin/python3 tests/run_all.py
RESULT: 29/29 scripts passed; 0 failed; exclusions: 0
python3 tests/run_all.py
RESULT: 29/29 scripts passed; 0 failed; exclusions: 0
```

This is validation of the implemented changes, not validation of the pending
gate relaxation. No tests were excluded or deleted from the existing suite.
