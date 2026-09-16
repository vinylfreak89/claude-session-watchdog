# Declaration-to-dispatch binding: finding and proposed contract

**Status: proposal, not an implemented repair.** The existing transcript permits
a human to establish that the two reported promises were kept. It does not
contain a machine-verifiable declaration-to-dispatch relationship. Automatically
clearing them under the current schema would require semantic inference by the
agent the instrument is meant to constrain. No production code, owner-authorized
disposition, or live state was changed. The two live alarms remain unresolved.

## What was verified

Read-only inspection of both declaring turns, the later dispatch input and its
receiver rollout confirmed the reported sequence. Both declarations have stable
assistant record UUIDs. Neither UUID occurs in the later dispatch input or the
receiver rollout. The receiver records contain a turn identity and the submitted
message, but no declaration or obligation link. The later brief carries the two
topics together, with different item numbering. The correspondence is clear to
a reader; it is not an existing foreign key or authenticated fulfillment record.

The relevant code boundaries are:

- `wd_check.py:151-157`: `turn_made_a_dispatch` accepts a tool-name substring or
  `codex-run`/`codex-app` anywhere in serialized input. It checks neither the
  promised work, the recipient, the result nor execution of the quoted command.
- `wd_check.py:182-208`: `owed` constructs declarations afresh, only for turns
  that still need a watchdog disposition. Once a turn is handled, its declaration
  disappears regardless of whether the target did the work.
- `wd_check.py:536`: `broken` filters those turn rows using one dispatch boolean
  per turn. One unrelated dispatch can therefore silence several declarations.
- `wd_lib.py:528-562`: `declared_actions` returns short regex matches, at most
  three. It drops the rest of the promise and its condition; it provides neither
  stable occurrence identity nor a complete work specification.
- `wd_lib.py:419-451`: the stronger `dispatches_in` deliberately rejects compound
  shell commands, including the real heredoc-plus-invocation shape. Inspection
  returned zero structural dispatches for that recorded turn. Substituting this
  parser would prevent some false credit but would not establish the missing
  binding or repair these two cases.

A later commit proves that work landed; its title does not establish which
promise requested it. Likewise, a receiver task-start record proves that a turn
started, not which earlier natural-language declaration it fulfills.

## Reproductions and controls

Run the executable specification from the repository root:

```
/usr/bin/python3 docs/reviews/reproduce_declaration_binding.py
```

Every case calls the real `wd_check.main` handler with synthetic transcripts and
temporary state. Session locations and the clock are fixture inputs. The prose
and dispatch commands are synthetic; no command in a fixture is executed. The
assertions express the requested behavior, not assertions that a defect remains.

| Deciding control | Current result | Required result |
| --- | --- | --- |
| `test_deferred_matching_dispatch_fulfils_promise` | 1 broken | 0 broken |
| `test_later_unrelated_dispatch_keeps_promise_owed` | 1 broken | 1 broken |
| `test_message_to_watchdog_is_not_promised_dispatch` | 1 broken | 1 broken |
| `test_same_turn_unrelated_dispatch_keeps_promise_owed` | 0 broken | 1 broken |
| `test_failed_dispatch_keeps_promise_owed` | 0 broken | 1 broken |
| `test_quoted_command_keeps_promise_owed` | 0 broken | 1 broken |
| `test_watchdog_message_alias_is_not_promised_dispatch` | 0 broken | 1 broken |
| `test_turn_hold_does_not_fulfil_target_promise` | 0 broken | 1 broken |
| `test_three_declarations_one_dispatch_leaves_two_owed` | 0 broken | 2 broken |

The requested negative control's failure, verbatim:

```
AssertionError: 1 != 0 : a later matching dispatch must fulfil the declaration
```

The additional false-closure failures, verbatim:

```
AssertionError: 0 != 1 : turn coincidence is not a work binding
AssertionError: 0 != 1 : a refused dispatch cannot fulfil a promise
AssertionError: 0 != 1 : writing a command in notes is not dispatching
AssertionError: 0 != 1 : recipient and work must be checked across tool aliases
AssertionError: 0 != 1 : watchdog turn disposition is not target action evidence
AssertionError: 0 != 2 : each declaration needs its own binding
```

The script exits **1**, reporting `Ran 9 tests` / `FAILED (failures=7)`.
The unrelated-later-work control and the exact `SendMessage`-to-watchdog shape
both pass. An in-memory mutation that credited any dispatch in the same or a
later turn made the negative control pass and the unrelated-work control fail:

```
AssertionError: 0 != 1 : a later unrelated dispatch must not receive credit
Ran 2 tests in 0.014s
FAILED (failures=1)
```

This is a falsifying reproduction delivered under the requested finding/proposal
option, **not a green implementation or a completed regression-test delivery**.
It is kept with review artifacts. No failing test has been added to the normal
suite and then excluded, skipped, or marked expected-failing. The required
negative control cannot honestly be green before a binding contract exists;
the existing suite's green result must not be read as evidence that this defect
is repaired. On implementation, move these cases into the normal suite unchanged
in their required outcomes, and add the receipt/provenance cases below.

## Proposed durable record

Store each declaration independently of the watchdog's response obligations.
Its identity should include the source session, immutable assistant record UUID,
text-block index and occurrence span, with a digest and the full source context.
Retain every occurrence, including those beyond the current three-match limit.
Never use the shortened display text or a timestamp alone as identity. Missing
UUIDs, edited records and ambiguous segmentation remain unbound.

Natural-language detection remains a candidate detector. The text "I'll send
this" supplies no mechanically complete subject, recipient or condition. A
candidate must not disappear because the operator failed to register it, because
its turn was answered/held/closed, or because later history fell out of a tail.
Do not change the owner's legitimate turn-disposition routes to achieve this;
track target promises separately from the watchdog's duty to respond.

Each binding records the declaration identity, frozen work-unit identity, intended
recipient, dispatch tool-use identity, independently recorded receiver request
identity, exact submitted payload digest and the record that authorized the
subject mapping. Preserve these records after fulfillment. Revalidation reads
the source artifacts; storing an operator-written `fulfilled=true` is not proof.

## How a verifiable binding could be produced

For future work, introduce a structured declaration/work-request envelope at the
producer boundary. It must freeze the requested work and recipient before the
dispatch, reference the declaration occurrence, and carry that identity through
submission into an independently recorded receiver receipt. The dispatcher must
deliver the frozen work as the actual request, not accept an arbitrary payload
plus a list of IDs that the operator claims it fulfills. A bundled dispatch may
fulfill two declarations only when the receiver accepts both corresponding work
units. One unrelated unit cannot consume the whole bundle's declaration list.

This proves delivery of the recorded work request, not successful implementation
of the requested work. Actual completion remains a separate acceptance check.

The unresolved semantic step is authorizing the mapping from free prose to that
frozen work request. A hash, exact quotation, matching filename, item number,
embedding score or operator-provided ID does not prove that mapping. Quoting an
earlier promise in an unrelated request must not count. Either the original
declaration must already be the structured request, or an independent trusted
reviewer must authorize the mapping. A second opinion controlled by the same
operator is not an independent authority.

The instrument should settle only when every required source and receipt is
present, unique, ordered correctly, addressed to the intended recipient, and
unchanged. Wrong recipient, rejected dispatch, missing receipt, ambiguous source,
unknown format or unreadable evidence remains OWED. It should report "unbound"
or "receipt unavailable" without asserting that a semantic promise was broken.
There must be no `fulfilled`, `force`, broad timestamp advance or similar verb
allowing the operating agent to substitute its assertion for these checks.

The existing bridge and receiver recorder would need to support this contract.
Their invocation or modification was not attempted during this review turn.

## Legacy promises, including the two reported cases

The old records do not acquire a structural binding merely because a new field
is added today. Clearing these cases honestly requires a trusted, retained
adjudication naming each exact source declaration and its exact accepted receiver
request, with the corresponding work units. The owner has already described the
semantic correspondence in the repair request; it still needs a source and
attribution contract the instrument can verify, rather than the operator copying
that statement into a JSON field. Use the same migration mechanism for all such
cases; never special-case their timestamps or topic words.

This is not a proposal to remove the existing owner-ack exception or modify hold
and closed. Those dispositions keep their existing owner-authorized meaning.
They do not manufacture dispatch evidence for a separate target promise.

The owner must choose and authorize the independent review/recording channel.
Without one, leave ambiguous legacy promises unbound. Preserve the existing
tracking frontier during migration; do not reopen pre-tracking history or use a
moving watermark to hide new promises. Keep a retained migration inventory so
reconciliation can detect missing declarations or bindings.

## Cost and adoption controls

The change spans declaration production, a protected subject-mapping authority,
dispatch envelopes, receiver receipts, persistent state, reconciliation and
migration. A watchdog-only parser change cannot supply those facts. Legitimate
legacy work will remain unbound until independently adjudicated. If the operating
agent can rewrite both the original evidence and the supposed authority record,
local hashes alone cannot provide the requested constraint; the recorder or
authentication boundary must be outside its control.

In addition to the nine reproductions, require handler controls for stable IDs
across polls/restarts, no loss on turn disposition, partial fulfillment of a
bundle, wrong recipients, failed/missing delivery, forged or quoted IDs, changed
payloads, missing authority, missing source records, malformed receipt fields,
reconciliation attempts to invent credit, and migration-frontier preservation.
No-op handlers and removal of any proof check must make those controls fail.

The next reviewable decision is the producer/authority/receipt contract above.
Until it is decided, leave closure behavior unchanged rather than install a
semantic guess as an instrument fact.

## Validation of this proposal-only change

Both existing-suite commands exited zero:

```
/usr/bin/python3 tests/run_all.py  # Python 3.9.6
RESULT: 29/29 scripts passed; 0 failed; exclusions: 0

python3 tests/run_all.py           # Python 3.14.7
RESULT: 29/29 scripts passed; 0 failed; exclusions: 0
```

The new review reproduction exits one on both interpreters, with the same seven
failed assertions out of nine. Read-only evaluation of the live `owed` rows also
confirmed `REPORTED_DECLARATIONS_STILL_BROKEN: 2`. No claim of a green repair is
made; adding the requested negative regression control to the green suite and
clearing those live rows remain dependent on the evidence-contract decision.

## Cost of a wrong binding in either direction

A false binding marks an unkept promise fulfilled. The reminder disappears,
unrelated work may consume another obligation's credit, and the owner can make
decisions on work that was never dispatched. If retained as trusted evidence,
the wrong binding can also contaminate later reconciliation. This is the silent
and more dangerous direction; missing or ambiguous evidence must not close work.

A missed binding leaves a kept promise owed. It causes repeated false accusations,
unnecessary investigation and possible duplicate dispatches. Over time an alarm
that keeps reporting completed work loses credibility and may be ignored when a
real obligation is missed. That cost is real; it calls for an auditable binding
and an honest "unbound" diagnosis, not an operator-controlled way to mute it.

The owner is choosing both costs when choosing the authority and migration
contract: stricter provenance prevents silent false closure but leaves more
legitimate legacy work requiring independent adjudication. Neither hashing a
guess nor widening a time window removes that tradeoff.
