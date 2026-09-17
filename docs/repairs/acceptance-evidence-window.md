# Keep historical duplicate IDs outside an acceptance's evidence window

`target_calls` previously populated its ID map from the entire target transcript,
then applied the receipt timestamp only when selecting successful results.
A duplicate from before the request therefore raised before any kind evaluator
could run. The common acceptance wrapper converted it into UNDECIDED.

The reader still inspects the full record, but it admits tool-use and tool-result
records into the evidence window only when their timestamps are **at or after**
the receipt. Historical records are not rewritten, deduplicated or downgraded to
warnings. Their IDs simply do not participate in this window's duplicate check.
An old record appended later in the file is still outside the time window; there
is no assumption that file position establishes chronological order.

Duplicate IDs inside the window, including exactly at the receipt timestamp,
still raise. The diagnostic now names the ID and the receipt boundary:

```
duplicate target tool-use id 'duplicated-control' in receipt window at or after 2026-09-16T10:00:10Z
```

Action credit remains stricter than that integrity boundary: a qualifying call
must be strictly later than delivery, with a successful result no earlier than
the call. A pre-delivery call with a later result cannot supply action credit.
An undated tool record cannot be assumed historical and remains UNDECIDED.
No override, transcript repair or acceptance-kind shortcut was added.

## Controls and failures

The existing `tests/test_acceptance_contract.py` now has 39 tests. All seven
`test_<kind>_historical_duplicates` controls invoke the real queue, receipt and
`owed` handlers. Each first requires NOT-YET in a clean window with earlier
duplicates, then produces the kind's actual action and requires the poll to
archive the item with PASS and retained action evidence. No acceptance decision
function is mocked in these controls.

Other controls exercise a duplicate exactly at receipt and later in the window
(with an otherwise satisfied file), old duplicates appended late, pre-delivery
and boundary-time calls with later results, and an undated tool call. The
in-window control checks both the direct EvidenceError and the real poll's
UNDECIDED verdict, with the queue item retained.

Against a scratch copy of `6e42e88`, the final controls reported:

```
FAIL: test_commit_historical_duplicates (__main__.AcceptanceContract)
FAIL: test_csv_historical_duplicates (__main__.AcceptanceContract)
FAIL: test_file_historical_duplicates (__main__.AcceptanceContract)
FAIL: test_grep_historical_duplicates (__main__.AcceptanceContract)
FAIL: test_late_appended_old_duplicates_do_not_poison_window (__main__.AcceptanceContract)
FAIL: test_msg_to_watchdog_historical_duplicates (__main__.AcceptanceContract)
FAIL: test_row_historical_duplicates (__main__.AcceptanceContract)
FAIL: test_task_historical_duplicates (__main__.AcceptanceContract)
FAIL: test_undated_tool_use_cannot_be_assumed_historical (__main__.AcceptanceContract)
Ran 39 tests in 3.237s
FAILED (failures=9)
```

The historical cases emitted `undecided: EvidenceError: duplicate target tool-use id`.
The undated-record case instead incorrectly settled an otherwise satisfied item;
its failure included this verbatim output:

```
SENT, NOT YET ACTED ON: 0  (closed this poll: Q1)
```

One initial fixture correction was necessary: the row control had no ledger at
all, so after the production fix it correctly reported:

```
undecided: EvidenceError: missing or mistyped boolean fact: present
```

The intended NOT-YET fixture now supplies an available, empty ledger before
capturing the baseline. It still requires a target-attributed row write to pass.
The corrected control was rerun against the baseline and still fails on the
historical duplicate, as shown above. No production predicate was relaxed to
hide the missing-ledger condition.

## Read-only live census

The initial snapshot reproduced 7,877 tool-use occurrences, 7,445 distinct IDs
and 432 duplicated IDs. Its single sent queue item returned exactly:

```
EvidenceError: duplicate target tool-use id
```

The target continued appending records during this work. For the final census,
the target records used by receipt lookup and `target_calls` were frozen in
memory. No acceptance poll handler was run against live state; the census called
`A.evaluate` on a copied state object using the actual configured sessions and
acceptance. Both the state object and state/config file bytes were checked for
changes after evaluation and were unchanged.

| Final snapshot population | Count |
| --- | ---: |
| Transcript records | 61,492 |
| Tool-use occurrences | 7,885 |
| Distinct tool-use IDs | 7,453 |
| Duplicated IDs | 432 |
| Queue items | 4 |
| Sent items eligible for acceptance | 1 |
| Unsent items, not evaluated as delivered work | 3 |
| Tool uses in the sent item's receipt window | 44 |
| Duplicated IDs in that window | 0 |
| Successful post-receipt calls | 41 |

**1/1 eligible sent item now reaches a verdict: NOT-YET. PASS: 0; UNDECIDED: 0.**
The engine-test item's deciding reason is:

```
no successful matching target reply call after delivery
```

The census explicitly required NOT-YET for that item. It was not settled,
re-recorded or edited. All 432 duplicated IDs are outside its window.
The seven-kind positive/negative fixture coverage is distinct from this one-item
live census; it is not a claim that seven live items or every transport shape
were exercised.

## Other acceptance inputs with whole-history reads

**Yes, other inputs read whole history.** None of the following imposes the old
unrelated-tool-ID duplicate guard, but fixing this guard does not make every
historical corruption irrelevant:

- Common receipt lookup, `wd_receipts.py:196`, parses the full target transcript,
  requires exactly one record with the selected delivery UUID, and splits the
  preceding records to locate the answered turn. Unrelated duplicate tool IDs
  do not fail that lookup. A duplicate of the selected delivery UUID still
  refuses intentionally. Malformed JSON anywhere can still make the strict
  `read_records` fail; malformed old turn structures can also obstruct the
  preceding-turn reader. No corrupt-record skipping was introduced.
- Task acceptance, `wd_acceptance.py:293`, rereads the target transcript and
  searches all turns for a session-scoped launch. It then requires a harness
  completion notification strictly after receipt. A launch before delivery can
  be legitimate evidence of a still-running task whose completion was requested;
  blindly truncating this input would lose it. Old duplicate IDs are not globally
  rejected here, but malformed history or an undated task notification can still
  make evaluation undecided.
- Reply acceptance, `wd_acceptance.py:318`, reads the watchdog transcript to find
  the actual matching delivery after the qualifying target send. Invalid delivery
  candidates raise EvidenceError locally and are skipped; invalid JSON in the
  whole transcript still refuses the read. Socket sender attribution additionally
  reads the sender transcript (`wd_receipts.py:102`), requiring uniqueness only for
  the matching transport message ID and corresponding source tool use. Unrelated
  duplicated IDs are not a global veto there.
- The `msg-to-watchdog` fact producer in `wd_check.check` reads the target's last
  six turns, not its whole history. That count is diagnostic; `evaluate_message`
  requires its type but independently checks full-window calls and actual delivery.
  A missing older call from that diagnostic tail is not the closure decision.
- File/grep/CSV/row acceptance reads the baseline and current subject file and uses
  the now-windowed target calls for attribution. Commit acceptance queries the
  specified commit and remote ancestry. Task output reads its exit-marker tail.
  These artifact/history queries are not duplicate checks over the target's full
  tool-use record and were not changed.

At the time of this repair, a separate format limitation was visible: reply acceptance
recognizes `mcp__ccd_session_mgmt__send_message` with a `session_id` recipient;
`SendMessage` is not a qualifying call name there. This scope repair does not
claim to fix that transport variant. The live window in the census contained
only successful Bash calls, so no reply-call format was present to adjudicate;
the required NOT-YET verdict is not being substituted for a known successful
reply. This limitation is now repaired and censused in
[Read both watchdog-message tool formats](message-tool-spellings.md), including
the additional real-result and recipient shapes needed for those messages.
Unknown timestamps and unreadable evidence remain reasons to stop, not permission
to close an item.

## Suite and scope

Both full-suite commands exited zero:

```
/usr/bin/python3 tests/run_all.py  # Python 3.9.6
RESULT: 28/28 scripts passed; 0 failed; exclusions: 0
python3 tests/run_all.py           # Python 3.14.7
RESULT: 28/28 scripts passed; 0 failed; exclusions: 0
```

This checkout already contained 28 test scripts at `6e42e88`; no script was
removed or excluded by this repair. Only the acceptance reader, its existing
control script and this repair record changed. Live transcripts, state,
configuration and queue items were not modified. No push was performed.
