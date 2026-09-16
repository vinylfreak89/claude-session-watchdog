# Send and acceptance review

Reviewed `5800a53`, `cc0fb3a`, and `bd3a5c8` on `main`, including their callers,
state writers, reconciliation and monitoring. Production code was not changed.
No live sessions were contacted or live state modified. No push was performed.

**Verdict: the claimed send/action guarantees do not hold.** In particular, the
findings send gate described in the commit message was not implemented, credited
messages can clear subsequent turns, and reconciliation can restore a refused mark.

The deciding probes are in [reproduce_send_acceptance.py](reproduce_send_acceptance.py).
Run `/usr/bin/python3 docs/reviews/reproduce_send_acceptance.py`. Its 18 assertions
assert the **unsafe behavior observed at this revision**: `OK` means reproduced,
not a passing security or regression suite. Test names below omit the `test_` prefix.
CLI probes call the real argument parser and handler, with only session lookup,
transcript location and time substituted. File/state operations use temporary files.

## Findings

### 1. P1: `sent` still discharges turns without any delivery

Location: `wd_wake.py:756`, especially `:774`; `wd.sh:57` dispatches to that handler.

`--sent` never calls `answered_allowed`, requires neither target nor sender, and
advances `last_send_ts` even if every supplied finding ID is nonexistent. In a
fresh synthetic state, `--sent F_DOES_NOT_EXIST` returned zero and cleared an
otherwise owed, already relayed turn with an empty target transcript. There need
not even be a message ID. This is exactly the unlocked sibling door the commit
claims to close. Existing findings are also removed from `proposed` without
checking delivery or retaining an action obligation.

**Reproduced:** `sent_without_delivery`. Require validation before any state or
finding-log mutation, and test the actual shell/CLI path.

### 2. P1: re-citing a message is not idempotent, and mark time launders old deliveries

Locations: `wd_check.py:235-245`, `:514`, `:673-677`, and `:303`.

An already credited caller-supplied ID returns true unconditionally. `sent1`
then stamps both the item and `last_send_ts` with **now**, not delivery time.
Send once, mark Q1, let another target turn finish, then repeat `sent1 Q1 M`:
the new turn disappears from `owed` without another message. The same M can also
mark Q2 even though the only transcript message carried Q1. Neither message ID
nor item content is matched against the delivery; the ID is just an alias chosen
by the caller.

Changing M to a *fresh* ID cannot spend the same timestamp twice: existing control
8 correctly tests that narrow case. Reusing M can. Even without ID reuse, delaying
the **first** `answered` mark until after another turn completes credits the old
message to that later turn. The timestamp watermark also clears every earlier
turn, rather than binding a receipt to one answered turn.

**Reproduced:** `reusing_message_id_clears_new_turn`,
`same_delivery_marks_unrelated_queue_item`, and
`delayed_first_mark_clears_post_delivery_turn`.

Persist a transcript-derived receipt and its fixed delivery time/answered turn.
Re-citing it may attach the intended findings and item; it must not advance the
acknowledgement frontier or attach arbitrary other items.

### 3. P1: reconciliation restores the effect of a refused send mark

Locations: `wd_recon_lib.py:1719-1724`, `:1787-1792`, `:369-375`;
`wd_reconcile.py:731-734` applies the derived repairs.

`restorations_owed` includes failed actions. `effect_disposition` translates a
missing `answered` effect into an advance of `last_send_ts` to the action's time.
`stage5_repair` applies that advance without delivery verification. Its additive
invariant preserves keys, but advancing this particular key removes obligations.
The `sent1` repair at `wd_recon_lib.py:1673` similarly supplies a `sent` field
without going through the new guard.

**Reproduced at the worklist/merge boundary:**
`reconcile_restores_refused_answer_without_delivery`: the real `answered` handler
first refuses an empty transcript; a synthetic replay action recording that
failure produces a repair; applying it clears the owed turn. Full reconciliation
CLI prerequisites were not exercised. They check reconciliation completeness
and supersession, not the delivery evidence missing from this repair.

A repair must restore a verified receipt, not fulfill the intended effect of a
command that correctly refused to run.

### 4. P1: changing acceptance after delivery is a disguised manual close

Location: `wd_wake.py:704-718`; closure at `wd_check.py:408-416`.

`queue acted-when` overwrites an existing acceptance on a sent item. A sent item
waiting for `file missing` can be changed to `file existing`, then the next poll
archives it. No target action occurred. There is no distinction between filling
an absent legacy acceptance and replacing an established requirement.

**Reproduced:** `change_acceptance_after_send_closes_without_action`.

Freeze the established acceptance once delivered. A necessary correction needs
separate, attributable authority; it cannot be indistinguishable from routine
metadata editing by the agent the check constrains.

### 5. P1: the acceptance table tests current state, not action on this item

Locations: `wd_check.py:331-340`, `:363-382`, `:408`.

`file existing` closes an item asking for that file to be rewritten even when its
mtime predates delivery by decades. `settle_acted` does not pass the item or its
send timestamp to the checker. Grep matches, row presence, CSV matches and an
unspecified-since watchdog message likewise have no automatic item/freshness
binding. A row-removal request cannot be expressed by `row ID`, whose passing
condition is that the row remains present. `task` means any exit, including failure;
that is completion evidence only, not success evidence.

**Reproduced:** `preexisting_file_is_action`. The other per-kind implications are
**reasoned from their producers and predicates**. Some requests legitimately ask
only for an already true condition; that does not justify treating these checks
as proof that a new requested action occurred.

The state distinction also fails to reach the send gate: `next_item` excludes sent
items and checks only unrelayed turns, so Q2 can be released while sent Q1 remains
unacted. **Reproduced:** `next_ignores_sent_unacted_item`, at `wd_check.py:439-460`.
This does not archive Q1, but contradicts the work-set pacing intent.

### 6. P2: new `queue drop` creates duplicate live queue IDs

Locations: `wd_wake.py:700`, `:737`.

The allocator counts live plus sent rows, but ignores dropped rows. Add Q1 and Q2,
drop Q1, add another item: the live queue is now `[Q2, Q2]`. The mutators find the
first match, so callers cannot reliably address the second obligation. Receipts,
acceptance changes and later reconciliation become ambiguous.

**Reproduced:** `queue_ids_collide_after_drop`. Use a persistent monotonic sequence
or identities independent of store lengths, as the owner-decision allocator does.

### 7. P2: chunk boundaries turn a real lifecycle event into a conclusive absence

Locations: `wd_lib.py:716-723`, `:753-760`; consumer `wd_wake.py:206`.

The backwards reader discards each chunk's initial partial line without carrying
it into the adjacent chunk. The adjacent chunk holds only the complementary
prefix. A `task_started` record spanning the boundary is lost or returned as
truncated JSON. Parse failure does not make `lifecycle_known` false. A valid
synthetic rollout with the event across the 4 MiB boundary returned
`last_started=None`, `in_flight=False`, `lifecycle_known=True`.

**Reproduced:** `lifecycle_event_split_across_chunks_is_lost`. This re-enables the
false `dispatch_no_turn` finding the commit intended to eliminate. Preserve whole
lines across reads; conclusive absence requires a complete, valid scan.

### 8. P2: the monitor still treats unknown lifecycle as finished

Locations: `wd_wait.py:190-194`, `:226-227`.

The new `lifecycle_known` flag is consumed by the wake's dispatch accusation but
not by `Watch._progress`. A found rollout with unknown lifecycle and
`in_flight=False` is reported as assessable with “no turn in flight.”
`interrogate` explicitly suppresses the stall because it believes the turn finished.
Thus a turn too large to locate its start can lose its stall alarm.

**Reproduced at the consumer boundary:**
`unknown_lifecycle_treated_as_finished_by_monitor`, injecting the exact unknown
fact shape; suppression follows directly from the cited branch. Unknown must
remain unknown throughout the consumers, not just at one finding site.

### 9. P2: dispatch detection retains the quoted-launch defect

Locations: `wd_lib.py:416-437`.

`background_launches` gained the input flag and session scope checks, but
`dispatches_in` gained only an anchor. It still accepts a foreign launch line at
the beginning of a foreground result. It also finds dispatch syntax inside quoted
command arguments. A foreground `printf` mentioning a dispatch and returning a
foreign launch line produces a dispatch with that foreign background-task ID.

**Reproduced:** `foreground_quoted_dispatch_is_a_launch`. No dispatch command was
executed by the probe. `test_launch_is_not_a_mention.py` exercises only
`Turn.background_launches`, not this sibling consumer.

### 10. P2: delivery detection still equates quoted text with provenance

Locations: `wd_check.py:195-222`.

The `user` branch checks for bare `from="SELF"` anywhere in the body. It does not
require a peer origin or even a cross-session wrapper. A human string-content
record quoting that attribute is accepted as the watchdog's delivery. Conversely,
a text-block array containing an otherwise identical real delivery is JSON-dumped,
re-escaping the attribute quotes and refusing the message.

**Reproduced with synthetic record shapes:** `human_quote_is_delivery` and
`real_text_block_delivery_is_rejected`. These do not establish how often those
shapes occur in live sessions. The first fails toward false delivery credit; the
second toward nagging. Verify channel provenance and decode text blocks before
matching content.

## Failure direction and additional edges

- **Missing fact can close:** the file predicate uses
  `modified_since is not False`. With an explicit since argument, a producer that
  returns `exists=True` but misspells/omits `modified_since` passes. Deciding probe:
  `missing_file_fact_fails_open`, a producer-shape fault injection. This is a latent
  schema failure, not a claim that the current file producer misspells the key.
- **Wrong type can kill the poll:** predicate evaluation occurs outside the
  exception handler. `hits='one'` raises `TypeError`, rather than yielding
  undecided. Deciding probe: `bad_fact_type_kills_poll`, also fault injection.
  Most missing keys nag, but the blanket claim that every wrong shape nags is false.
- **Unknown kinds are accepted when queued:** `queue add --acted-when running`
  stores the spec without validation (`wd_wake.py:699-703`). Later `sent1` refuses
  it, so this currently fails safe, but property 4's “at the moment it is set” is
  not met. Deciding probe: `unknown_kind_accepted_on_add`.
- **“Never sent” means “never marked”:** `queue drop` consults only `item.sent`.
  Deliver Q1 and omit/fail `sent1`, then drop Q1: accepted even with the delivery
  present in the transcript. Deciding probe:
  `delivered_but_unmarked_item_can_be_dropped`. Checking property 6 against actual
  delivery needs the item/receipt binding absent in finding 2.
- **Explicit trust exits remain:** `answered --owner-ack`, `closed`, `hold`, and
  `resolved` trust supplied words/dispositions. Some are intentionally authorized
  exceptions in the standing skill; this review does not label their existence a
  regression. They prevent describing the whole system as evidence-only unless
  owner authority is independently authenticated. Direct state-file writes are
  also outside what these CLI guards can prevent under the same writer identity.

## The table and the controls

Delete the **parallel untyped fact-to-boolean table**, not the concept of acceptance.
Per-kind semantics cannot be inferred from generic truthiness: zero matches may
mean successful removal; an exit may mean failure; an existing row may be the defect.
Each supported acceptance should return a typed, explicit pass/not-yet/undecided
result with its evidence and the requested postcondition. Validate that contract
at creation, freeze it at delivery, and have closure consume a receipt tied to the
item and the required time/baseline. Merely moving today's lambdas into `check`
would not fix the meaning or attribution problems.

All **16 non-reconciliation test scripts passed**. The six declared pre-existing
reconciliation CLI failures were not rerun or treated as findings. The reconciliation
probe above isolates a different worklist/repair behavior.

The 20 controls in `tests/test_sent_needs_action.py` have useful helper-level
positive tests; a completely inert `settle_acted` would fail controls 12-14.
They do **not** establish the CLI or whole-system properties:

- No control invokes `sent`, `sent1`, `answered`, `owed`, `queue drop`, or
  `queue acted-when` through their command handlers.
- Control 7 checks that the helper returns true for an already credited ID, but
  never checks that the caller's repeated mark leaves `last_send_ts` unchanged.
- Only `file` has a successful action/closure control. The malformed CSV control
  accepts rejection of the entire kind just as readily as an exception from its
  implementation. Its name claims more than its assertion establishes.
- Control 17b claims to check that the missing artifact “names itself,” but asserts
  only the phrase `no such file`, not the file path.

**Deciding mutation:** keep only the `file` entry in `C.ACCEPTANCE`; replace both
`C.main` and `K.main` with `lambda: 0`; run the existing suite's `main`. Output:
`all checks passed`. Thus all six other kinds and all CLI behavior can do nothing
useful while this suite remains green. Reproduce with:

```python
import runpy
import wd_check as C, wd_wake as K
C.ACCEPTANCE = {'file': C.ACCEPTANCE['file']}
C.main = lambda: 0
K.main = lambda: 0
raise SystemExit(runpy.run_path('tests/test_sent_needs_action.py')['main']())
```

Add deciding controls at the receipt-to-state boundary, with multiple turns and
items, both allowed mark orders, repeated marks after a later turn, failed marks
followed by reconciliation, and actual CLI settlement. Add successful and failing
real-producer cases for every supported acceptance, and boundary/unknown-consumer
controls for lifecycle detection.

## Validation notes

All 18 final review probes reproduced. Initial scratch harness failures were
`KeyError: 'ts'` (an incomplete queue fixture) and
`AttributeError: module 'wd_wait' has no attribute 'Watcher'. Did you mean: 'Watch'?`
(the probe used the wrong class name). Both fixtures were corrected and rerun;
neither is a production finding. A lock liveness probe returned
`PermissionError: [Errno 1] Operation not permitted` under the sandbox. The
orchestrator-owned lock's owner/root were verified before writing these review
artifacts; no lock was acquired, replaced or removed.
