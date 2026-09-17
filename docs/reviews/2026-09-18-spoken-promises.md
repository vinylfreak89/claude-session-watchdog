# Review: spoken promises, including symmetric scope

Reviewed `docs/proposals/spoken-promises.md` at `a97b4f0` (proposal `eeee7d6`
plus the owner's symmetry ruling). **Do not build it as written.** A symmetric,
plain-language account of commitments is useful, but the proposed content-blind
check is unspecified, and truthful accounting is presented as a stronger
constraint on doing the work than it actually is. I support the reporting purpose;
I do not accept the current guarantees or the stated implementation cost.

This review does not revive the rejected declaration-identity contract. Retaining
what an agent actually said and exposing uncertainty does not require mechanically
proving the meaning or fulfillment of its promises.

No mechanism or live state was changed. `probe_spoken_promises.py` exercises
existing readers with synthetic inputs. There is no proposed handler to test yet:
reader observations below are reproduced; attacks on the proposal are reasoned
counterexamples, not claims of having run a nonexistent implementation. Named
future controls specify what would decide implementation questions.

## 1. P1 — The central comparison requires the understanding it disclaims

**Proposal: lines 29–44. Reproduced mismatch; reasoned design consequence.**

Its previous item says “sending the pool-test brief to Codex once the eight runs
finish.” Its acceptable next account says “sending the pool-test brief: done,
dispatched at 14:38, commit f504c16.” These are neither equal nor does the second
contain the first. Exact comparison therefore flags the proposal's own example.
Recognizing them as the same promise needs a reading of their meaning or an
explicit reference to the earlier statement. The proposal specifies neither.

Counting lines or accepting similar prefixes fails the other way. An agent can
replace “send both reviews to the owner” with “reviewed whether to send a review,”
and give a true account of the smaller task. Bundling two old promises into one
answer can hide the unaddressed second promise. Repeating the entire old list
inside a quotation would satisfy a substring-presence check without accounting
for any of it. Exact references alone would establish which words were addressed,
not whether the answer addresses them honestly.

**Change first:** preserve and display the earlier words beside the agent's
account; say explicitly who judges correspondence. If it is the agent/human
reading prose, describe that as a judgment, not a trivial content-blind guarantee.
Do not install approximate string matching as fulfillment evidence.

**Deciding controls:** `test_proposal_paraphrase_is_not_a_disappearance`, paired
with `test_narrower_task_does_not_account_for_original` and
`test_quoted_previous_list_is_not_an_account`. The first must succeed without
making the other two succeed. Until the comparison behavior is specified, there
is no honest expected result for a purported mechanical disappearance test.

## 2. P1 — Literal, truthful compliance can produce no work indefinitely

**Proposal: lines 32–34, 48–57, 80–82. Reasoned counterexamples.**

An agent can list each promise, then write: “I am abandoning this because I have
chosen not to work on it.” That can be true, accounts for the exact promise, and
is expressly a complete discharge. Alternatively it can carry every sentence
unchanged forever, truthfully, without doing anything. Neither attack depends on
under-reporting, lying, a parser defect or a lossy record.

These are not necessarily defects in an *accounting* instrument. Making honest
abandonment possible is valuable. They do defeat the stronger inference that
satisfying this instrument establishes follow-through or makes evading work
harder. A liar writing “done” would violate the truth obligation even if the
instrument accepted it; that is a separate enforcement limit, not literal honest
compliance. The proposal has not established that a human will detect the lie or
that agents will find it harder than satisfying a schema.

The document correctly leaves queue acceptance and underlying verification in
place. Keep that separation explicit: accounting for an abandoned promise is not
permission to cancel an owner's instruction, and “done” is a reported claim,
not verified work. Self-generated plans and duties assigned by the owner need
not have the same consequences when abandoned; the human can assess that from
prose without a new declaration schema.

**Owner judgment:** whether truthful disclosure alone is the intended success
criterion, who may abandon assigned work, and when indefinite deferral warrants
intervention. No unit test can prove the comparative thesis about honesty.
**Deciding boundary control:** `test_truthful_abandonment_is_visible_but_does_not_close_assigned_work`.
A stub that always returns “still open” demonstrates the permitted liveness limit;
it must not be advertised as evidence of progress.

## 3. P1 — Missing accounts and missing baselines are not empty lists

**Proposal: lines 22, 32, 41–44, 74–76. Lifecycle omission reasoned;
reader behavior reproduced.**

Disappearance comparison presupposes two observable lists. A crashed turn may
produce neither a final message nor a next turn. Omitting the *whole* next list
is not the disclosed hole of never registering an individual promise. If the
observer waits indefinitely for the next list, all already-registered promises
can escape accounting. Treating the missing list as empty instead invents
abandonment or disappearance after an interruption the agent could not control.

| Condition | What must remain observable; deciding future control |
| --- | --- |
| Context compaction, raw record intact | Retrieve the previous verbatim account; do not substitute the summary. `test_compaction_retains_previous_account` |
| Resume with the same session | Resume the saved account; “first list starts empty” applies to initial adoption, not every restart. `test_resume_does_not_reset_previous_list` |
| Crash before publishing an account | Retain the last complete account and report no new account observed, not no promises. A promise first spoken in the crashed turn remains an acknowledged coverage gap. `test_crash_preserves_previous_account` |
| Interrupted or token-truncated turn | Distinguish an incomplete account from a completed empty account. `test_interruption_is_not_discharge` and `test_truncated_final_is_not_empty_list` |
| Observer crash after reading but before saving | Recovery must not skip that account or replace a newer one with a replay. `test_observer_restart_recovers_unstored_account` |
| Transcript lost/truncated or a torn record | Retain the last good saved account and report continuity unknown. If both sources are lost, the promise contents cannot be reconstructed honestly. `test_missing_history_is_unknown_not_clean` |
| Concurrent accounts | Preserve each actor's account independently; duplicate delivery or a late older snapshot must not replace the current one. `test_concurrent_actors_do_not_overwrite` and `test_stale_account_cannot_replace_newer` |

Compaction of model context is **not automatically loss of the raw transcript**.
Do not invent that failure. But the promise of “nothing may silently disappear”
cannot extend to evidence that has actually been lost. The cost of durable
recording, recovery and retaining attribution is missing from “no new state beyond
the previous list.” These are recording responsibilities, not proof-of-work rules.

Existing code cannot be reused indiscriminately: `wd_lib.py:312` classifies an
assistant `max_tokens` response as `end_turn`; an interrupted response is
`interrupted`; a turn without a final response stays `open`. The probe verifies
all three without any promise list. `wd_lib.py:195` silently drops a torn JSON
record; `last_turns` at line 403 uses a bounded tail. Neither absence is evidence
that the agent's previous account was empty. The existing strict receipt reader
shows that unknown evidence can instead be reported explicitly. No new reader
is implemented here.

The existing transaction lock (`wd_state.py:7`) helps serialize supported state
writers, but cannot distinguish whose list is being overwritten or whether an
incoming snapshot is older. A concurrency lock alone does not settle symmetry.

## 4. P1 — Stored speech is not speech the human will see

**Proposal: lines 29, 50–52, 74–82. Reasoned delivery/retention gap.**

“Plain language a human will read” supplies the deterrence argument, but there is
no specified path from the stored list to the human. An agent can give an
abandonment account while the owner is away, then start another turn with an
empty list. A store containing only the latest open list can now be perfectly
clean while the owner never sees the abandonment. A full retained transcript
might preserve it, but the proposal neither requires that retention nor makes
the account visible on the owner's return. Accessibility is not actual review.

The watchdog also writes and audits its own account. Symmetry of obligations
does not itself provide independent observation of that account. Missing or
unreadable self-reports must be visible through the same review surface as a
target's missing report. This need not become a new independent adjudicator:
the existing human must simply be able to see what all three actually said.

**Change first:** identify the human-facing review surface, retain the prior
words and disposition long enough for later inspection, and display unreviewed
accounts without implying they have been verified. Do not require the human to
approve each routine line; whether review or acknowledgement gates anything is
an owner decision, not a repair I authorize here.

**Deciding control:** `test_owner_away_sees_abandonment_after_later_empty_lists`,
repeated for each of the three actors. A store write alone does not pass it.

## 5. P2 — Advisory detection can help, but its proposed question overclaims

**Proposal: lines 65–70. Detector behavior reproduced; usefulness is judgment.**

A source-linked “did you mean to make a commitment here?” prompt can be useful.
Changing an accusation into an uncertain question is a real improvement **if**
it no longer creates an owed turn, delivery demand or automatic send. Merely
changing the headline while retaining those consequences would be cosmetic.

The proposed wording still asserts “it is not on your list,” which requires the
same missing semantic comparison as finding 1. Use the actual source passage and
ask the agent to inspect it; do not claim absence from a paraphrased list.
`declared_actions` returns fragments, not full promises (`wd_lib.py:561`):

```
deferred ["I'll hand this"]
matching_open ["sending the pool-test brief to"]
past_tense ["to Codex for"]
quoted ["I'll send"]
non_dispatch_promise []
four_promises ["I'll send", "I'll send", "I'll send"]
```

The probe's past-tense sample is “I sent the brief to Codex for review.” Its quoted
sample is a test fixture quoting a promise, not the speaker making one. Its
non-dispatch sample promises to recheck a truth file. Four separate send promises
hit the default three-match cap and become three indistinguishable fragments.
Do not treat this detector's output as a completeness check or as the source
context the reviewer needs.

Questions are not free here: sending them can open user turns and obligations.
Even private repeated prompts can habituate the operator to dismiss them. Show
an advisory without promoting it to an unverified accusation; measure whether
it finds otherwise-missed commitments over representative complete turns. If
it yields repetitive noise without useful discoveries, delete it. Keeping an
existing function is not itself a reason to retain the feature.

**Deciding controls:** `test_quoted_fixture_advisory_creates_no_obligation`,
`test_paraphrased_list_is_not_asserted_missing`, and
`test_repoll_does_not_resend_same_advisory`. The value of the remaining questions
needs an observed review sample and the owner's judgment, not a green regex test.

## 6. P2 — Symmetric responsibility needs different observation points

**Proposal: lines 22, 32, 89–93. Rhythm consequence reasoned;
Codex-reader limitation reproduced.**

I accept symmetry; none of the obstacles below justifies exempting me. They do
mean the same end-of-turn ceremony cannot be assumed to cover every actor:

- A watchdog can wake every minute with no new promise. Repeating the entire
  account can bury changes. Showing that it is unchanged must preserve access
  to the actual preceding words; it must not mean the account vanished.
- A dispatched Codex task may finish once and never receive another turn. Its
  last open promises must remain visible without requiring it to manufacture
  another dispatch. Continuation belongs to the orchestrator; I cannot invoke
  it myself. `test_final_dispatch_with_open_promise_remains_visible_without_resume`
  should exercise this case. When to require another account is for the owner.
- A target may be interrupted mid-answer; a truthful accounting obligation does
  not make a final answer physically possible. Mark the account unavailable and
  preserve the earlier one; do not accuse the target of silently dropping work.
- The actual Codex lifecycle helper returns only a **200-character** shortened
  final-message summary (`wd_lib.py:818`). A synthetic, completed task with `OPEN:`
  after that point has a known completion and no `OPEN:` in the returned summary.
  `test_full_codex_account_survives_lifecycle_summary_truncation` must use the full
  authored record, not that digest. Claude and Codex need appropriate readers;
  symmetry is not permission to pretend their formats are identical.

## Recommendation and remaining uncertainty

Revise this into an explicitly human-reviewed accounting aid: retain authentic
prior words, show the agent's account beside them, expose missing evidence, and
keep reported completion separate from verification and owner-assigned duties.
Specify recovery and the human review surface before claiming disappearance is
a solved mechanical check. Preserve the benefits of honest abandonment without
calling it authorization to disregard assigned work. Do not restore the rejected
work-identity/receiver-contract design under different names.

The largest judgment question is whether conspicuous, durable, truthful reporting
will change agent behavior enough to justify the attention cost. I cannot prove
or disprove the owner's behavioral thesis with these reader probes. I can show
that the current proposal does not establish it and permits truthful no-work
compliance. That distinction should be put to him explicitly.

Validation: the probe produced the same output under Python 3.9.6 and 3.14.7.
The existing 29-script suite was not rerun: no executable mechanism or existing
test was changed. The new file is a review probe, outside the regression suite,
not a claimed acceptance test or a replacement implementation. Live state,
configuration and queue items were not accessed for this review.
