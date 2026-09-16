# Send and acceptance repair

The command handlers, not helper return values, are the control boundary. All
fixtures use synthetic transcripts and temporary state; no live session is sent
anything. No override or force mode is introduced. Owner-authorized dispositions retain their own audited path.

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
obligations. Bare acknowledgements and owner-ack arguments cannot supply delivery evidence.
Owner-authorized hold and closed dispositions are handled separately, as described below. Legacy receipts without structural peer origin
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

## Corrected scope: owner dispositions and migration

The first receipt repair removed hold/closed dispositions too broadly. The owner
clarified their authority: RELAY AND (RESPOND OR HOLD), and the D15 one-line
exception. They are restored through real command handlers with nonempty reason,
acting session, governing ruling, timestamp, target identity and the hash of the
exact completed turn. A different turn or altered transcript cannot reuse one.
Relay remains required for a hold. Existing audited records cannot be rewritten.

The migration is automatic and one-time for an existing bootstrap. It freezes the
pre-upgrade bootstrap timestamp and legacy send frontier in `turn_tracking`
before upgraded handlers change anything. This preserves the historical policy
without letting a later send, boot or poll advance that legacy frontier. It does
not retroactively reopen pre-watchdog history. New receipts remain individually
bound. There is no CLI argument for a cutoff. Tests cover both boundaries.

The seven disposition/migration controls were observed red (`FAILED (failures=6)`)
and then green. During implementation an import alias collided with the existing
local turn variable (`UnboundLocalError: local variable 'T' referenced before
assignment`); the alias was corrected. A fixture that queued after its synthetic
delivery correctly received `REFUSED: item must have been recorded before
delivery`; its clock was corrected.

Question detection is conservative: explicit question punctuation (including the
full-width form), interrogative/request language, and outbound message text block
hold/closed. It is not a semantic proof that arbitrary natural-language text has
no direct question. The owner was asked whether a separate semantic review is
required; this limitation is not represented as a solved language-understanding
problem. Legacy disposition records missing attribution remain unauditable beyond
the frozen historical frontier; no author or ruling is fabricated for them.

## Typed acceptance and action attribution

Each kind returns `pass`, `not-yet` or `undecided`. Parsing, fact-shape validation,
producer errors and evaluator exceptions share the undecided boundary. Unknown
kinds and malformed arguments are refused when set. Baselines are captured before
delivery; missing legacy baselines cannot be manufactured after delivery.

The 25 real-handler/real-producer acceptance controls were observed red:
`FAILED (failures=15, errors=1)`, then all passed. Every kind has a successful
closure control, a pre-existing-state control, and an unattributed-change control.
The producer-schema fault injections are through `owed`, not helper-only tests.

Postconditions are intentionally specific:

- `file`: changed bytes and mtime after delivery, with successful target Write/Edit
  calls whose reconstructed contents equal the current file. A touch, failed write,
  other writer or unchanged contents cannot pass.
- `grep`: the above attribution plus newly matching lines.
- `csv`: the above attribution plus newly matching complete rows; malformed columns,
  rows or non-finite numeric comparisons are undecided.
- `row`: a target-created or changed ledger row. This kind cannot prove removal.
- `commit`: the full immutable hash was absent from live origin branches at baseline,
  and a successful direct target `git push origin ...` after delivery is corroborated
  by live remote reachability. Cached remote refs cannot pass it.
- `task`: an unfinished baseline, a launch scoped to the target session, exit zero,
  and a post-delivery harness completion notification.
- `msg-to-watchdog`: an exact expected reply, a successful target send call after
  delivery, and structural delivery evidence in the watchdog's transcript.

Unsupported-but-legitimate cases remain owed: generated artifacts without a
verifiable Write/Edit content history, ambiguous shell commands, already-published
commits, row removal, missing harness provenance, and sent legacy requirements
without baselines. Findings without a defined action postcondition remain explicitly
undecided; verified delivery alone does not turn them into completed work. No new
flag or setter can retrofit evidence after delivery. The ordinary send gate waits
for unacted work; the existing owner-authorized urgent pacing exception does not
close any obligation.

Mutation controls confirm that no-op check/wake handlers, specifically no-op owed
and finding-sent handlers, and removal of **each** of the seven kinds are detected
by the positive command-handler controls. `tests/test_acceptance_mutations.py`
passes only when those broken implementations make their deciding controls fail.

## Queue identities

All three real-handler identity controls failed before the repair and pass after
it. The monotonic sequence is seeded from live, sent and dropped history, including
the highest dropped ID. Existing ambiguous live IDs refuse sends; the repair does
not silently rename historical records.

## Lifecycle reads

Six controls through `check dispatch` were observed failing before the repair and
passing after it; the original 12 lifecycle controls also pass. The scanner carries
partial lines across chunks, validates actual event records rather than marker
mentions, obeys its byte cap, and treats malformed/incomplete reads as unknown.
Both start and completion events are covered at the 4 MiB boundary. The CLI exposes
lifecycle certainty explicitly so a caller need not infer it from `in_flight`.

## Lifecycle consumers

The real monitor control initially failed with `AssertionError: 3 != 0` and a
`HEARTBEAT` instead of a stall for malformed lifecycle records. It now passes:
unknown lifecycle remains assessable only as unknown and cannot quiet the monitor.
A real wake/bootstrap control additionally verifies that an apparent completion
with unknown lifecycle does not prune in-flight work; a conclusive completion does.
All three controls in `tests/test_monitor_lifecycle.py` pass.

## Launch attribution

Seven real wake/bootstrap controls were observed red (`FAILED (failures=6)`), then
green. A quoted `codex-run` command is not a dispatch; foreground output cannot
supply background identity. The shared launch reader now requires Bash, boolean
`run_in_background: true`, a successful result, and an exact session/tasks/id.output
path structure. The original nine launch-reader checks still pass. Compound shell
commands and wrappers remain unproven dispatches; no permissive fallback parses
quoted content as executed code.

## Finding withdrawal

The sibling `veto` command could still withdraw a delivered but unmarked finding.
Its three real-handler controls first reported `FAILED (failures=2)`; all now pass.
Veto retains its authorized role for never-delivered findings, but delivery
ambiguity and unreadable transcripts block it. The shell wrapper passes the same
configured target and sender as the receipt handlers.

## Reply reminders across mark order

The receipt refactor initially omitted the existing reply-window side effect.
Two real-handler controls exposed this (`FAILED (failures=2)`) and now pass.
The central receipt writer starts the reminder from delivery time, records a poke
without postponing its deadline, and never resets the window on a repeated mark.
Wake captures the existing configured reply interval on the proposal, so either
mark order uses the same recorded interval. No new CLI option was added.

## State-writer integrity

Three additional command-handler controls failed (`FAILED (failures=3)`): a
corrupt state file was replaced by a new empty store, concurrent queue additions
lost one item, and mutable default stores leaked between state directories. All
now pass. Existing unreadable/non-object state refuses mutation; defaults are
independent copies. Check, wake and reconciliation merge serialize their complete
read/modify/write transactions with the same per-state-directory lock. This is
not a bypass flag and does not make direct file tampering trustworthy.

## Attribution follow-up

Two adversarial controls exposed defects in the first typed acceptance repair:
`test_unrelated_push_cannot_attribute_commit` and
`test_missing_task_baseline_exit_is_undecided` both failed. Both now pass.
A successful target push must name the requested full hash as the source of an
explicit `HASH:refs/heads/BRANCH` refspec. A generic push of a mutable branch cannot
prove which commit that call published, and remains unverified. A missing task
baseline exit is explicitly undecided, not inferred to mean unfinished.

## Control audit and operator guidance

The old send-gate helper fixtures reported `RESULT: 3 FAILED` because their queue
items lacked acceptance and baselines. They were replaced with six real command
controls covering ready, busy, unrelayed, held, empty and owner-urgent cases; all
pass without weakening the gate. The quoted-delivery control now has an otherwise
valid registered payload, so unrelated content rejection cannot make it pass.
A mutation that fabricates sender provenance is detected by that control.

Eight mutation tests now cover each of the seven removed kinds, no-op whole
handlers, and specifically no-op owed, sent1, answered and finding-sent branches.
A separate status control requires `not-yet` for an absent artifact and names its
path; producer/schema failures require `undecided`. Usage now names the target
receipt UUID and the pre-delivery requirement boundary. `due` points to `next`
instead of telling the operator to send a batch.

## Review reproduction coverage

The historical review probe deliberately asserts unsafe behavior and is preserved
as a record, not run as a regression suite. Each of its 18 cases now has a command
handler control below (test names omit the `test_` prefix):

| Historical reproduction | Deciding control in `tests/` |
|---|---|
| reconcile_restores_refused_answer_without_delivery | test_reconcile_send_guard.py: stale_answered_repair_cannot_advance_send |
| delivered_but_unmarked_item_can_be_dropped | test_sent_needs_action.py: delivered_unmarked_item_cannot_be_dropped |
| next_ignores_sent_unacted_item | test_sent_needs_action.py: next_blocks_sent_unacted_item |
| sent_without_delivery | test_sent_needs_action.py: unknown_finding_cannot_record_send |
| reusing_message_id_clears_new_turn | test_sent_needs_action.py: same_receipt_does_not_answer_later_turn |
| same_delivery_marks_unrelated_queue_item | test_sent_needs_action.py: one_receipt_cannot_mark_two_items |
| delayed_first_mark_clears_post_delivery_turn | test_sent_needs_action.py: delayed_answer_does_not_answer_later_turn |
| change_acceptance_after_send_closes_without_action | test_sent_needs_action.py: sent_requirement_is_immutable |
| preexisting_file_is_action | test_sent_needs_action.py: preexisting_file_does_not_close |
| missing_file_fact_fails_open | test_acceptance_contract.py: missing_file_fact_is_undecided |
| bad_fact_type_kills_poll | test_acceptance_contract.py: bad_count_type_is_undecided_not_a_crash |
| unknown_kind_accepted_on_add | test_sent_needs_action.py: unknown_acceptance_refused_on_add |
| queue_ids_collide_after_drop | test_queue_identity.py: drop_cannot_reuse_a_live_id |
| human_quote_is_delivery | test_sent_needs_action.py: human_quote_is_not_delivery |
| real_text_block_delivery_is_rejected | test_sent_needs_action.py: text_block_delivery_is_usable |
| lifecycle_event_split_across_chunks_is_lost | test_lifecycle_handlers.py: start_across_chunk_boundary_is_in_flight |
| unknown_lifecycle_treated_as_finished_by_monitor | test_monitor_lifecycle.py: unknown_lifecycle_still_nags |
| foreground_quoted_dispatch_is_a_launch | test_launch_handlers.py: quoted_command_is_not_dispatch |

Final full run: **27 test scripts pass**; `tests/test_reconcile.py` reports exactly
the same six pre-existing missing-ledger CLI failures quoted above. Its stage-5
receipt-bypass controls pass. No live sessions, state or target files were changed,
and no network remote was used for the synthetic acceptance tests.

## Owner-ack authority question

The old owner-ack control attributes that exception to an owner ruling too. The
repair currently refuses operator-supplied acknowledgement words without an
independently verifiable owner record. Whether to preserve that old route pending
a separate hardening proposal was explicitly raised for clarification; its owner
authority must not be silently conflated with the unverified CLI representation.
There is no authenticated owner-ack replacement in this change.

The standalone compile check first hit the sandbox's external bytecode cache:
`PermissionError: [Errno 1] Operation not permitted: '/Users/vinylfreak89/Library/Caches/com.apple.python/Users/vinylfreak89/Documents/claude-session-watchdog/wd_acceptance.cpython-39.pyc.4340495728'`.
Repeating with `PYTHONPYCACHEPREFIX=/tmp/wd-repair-pycache` passed, as did `bash -n wd.sh`
and `git diff --check`. This was a cache-write restriction, not a syntax failure.
