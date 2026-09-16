# Send and acceptance repair

The command handlers, not helper return values, are the control boundary. All
fixtures use synthetic transcripts and temporary state; no live session is sent
anything. No override, force mode or operator-attested closure is introduced.

## Red baseline

`/usr/bin/python3 tests/test_sent_needs_action.py` against `6e2029c`:

```
Ran 15 tests in 0.077s
FAILED (failures=18)
```

Failures include subtests. Each later repair names its deciding control. An initial
fixture-only run failed with `AttributeError: ... wd_lib ... does not have the
attribute 'activity_ms'`; the nonexistent patch was removed before this baseline.

## Boundaries

A transcript receipt must have structural sender provenance, a stable record ID,
and an actual delivery record. Queuing and quoted text cannot supply one. A repeat
must preserve the first receipt's timestamp and turn binding. A sent requirement
is immutable. Passing action evidence must be newer than delivery and attributable
to the target; missing evidence is undecided or not-yet, never pass.

These constraints govern supported commands and reconciliation. They cannot make
files tamper-proof against a principal that can directly rewrite both transcripts
and state. Enforcing that stronger property requires a separate protected writer
and authentic records; no CLI flag can create that trust boundary.

## Receipt binding

Finding and queue marks now share a target transcript UUID, a fixed delivery time,
and exactly one prior completed turn. All payload components are matched exactly
and recorded together, so item-first and finding-first marking have identical
state. Arbitrary aliases and another item cannot reuse the receipt. Old timestamp
watermarks no longer discharge turns, and unanswered turns cannot age out of an
8-turn read window.

`answered` now requires a target delivery UUID and a body matching registered
obligations. Bare acknowledgements, owner-ack arguments, manual closed-turn marks
and held-turn metadata cannot supply independent action evidence. These former
exceptions no longer close turns. Legacy receipts without structural peer origin
metadata remain unverifiable, including attachments that carry only quoted text.
No migration flag or guessed provenance is provided.

## Reconciliation

The real `reconcile --stage 5 --apply` controls ran red with two failures, then
passed all three controls after the repair. Persisted pre-upgrade worklists cannot
advance send watermarks, manufacture receipts, mark items sent, inject acceptance
or closure fields, or restore lost obligations directly into the sent archive.
Lost items return to the live queue. Restoring restrictive metadata still works.
Reconciliation cannot reconstruct delivery when the receipt cannot be verified;
that legitimate historical repair remains impossible rather than being guessed.

The full existing reconciliation script has the same six pre-existing failing
CLI groups before and after the repair. Its old helper control expecting an
unmarked send to be restored was updated to expect refusal. During that update,
the old assertion produced `TypeError("'NoneType' object is not subscriptable")`;
that was an obsolete expected repair payload, not a new runtime failure.

Final existing-suite output (before and after):

```
RESULT: 6 FAILED: CRASHED test_cli_readings: FileNotFoundError(2, 'No such file or directory'), CRASHED test_reaches_a_hundred: FileNotFoundError(2, 'No such file or directory'), CRASHED test_corner_cases: FileNotFoundError(2, 'No such file or directory'), CRASHED test_robustness: FileNotFoundError(2, 'No such file or directory'), CRASHED test_cli_stages: FileNotFoundError(2, 'No such file or directory'), CRASHED test_cli_all_stages: FileNotFoundError(2, 'No such file or directory')
```

## Immutable delivered requirements

`queue acted-when` and `queue drop` now require a conclusive transcript check that
an item was never delivered. A saved sent mark, a delivery not yet marked, an
ambiguous matching record, or an unreadable transcript blocks both operations.
Missing legacy acceptance cannot be filled after delivery. It remains owed.
