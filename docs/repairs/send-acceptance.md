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
