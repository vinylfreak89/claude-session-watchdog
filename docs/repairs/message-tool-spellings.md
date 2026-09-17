# Read both watchdog-message tool formats

The message readers now accept exactly `SendMessage` and
`mcp__ccd_session_mgmt__send_message`, through `wd_lib.MESSAGE_TOOL_NAMES`.
A suffix or substring match no longer admits unrelated tools. Acceptance,
`messages_to_watchdog`, reconciliation's outgoing-message readers, and the
question detector use the same input reader. The question detector must see a
question carried by the current tool before deciding whether a turn can be held
or closed; this repair does not change that rule.

## Input, recipient and result evidence

The legacy input supplies `session_id` and `message`. The current input supplies
`to` and/or `recipient` and `message`; conflicting addresses are refused. A
current non-message operation is not a reply. Actual current inputs also contain
`content`, whose value differs from `message`; it is not substituted for the
message that the receiver actually receives.

A canonical session ID or its full bare ID identifies the same recipient. No
prefix search, display-name match or persistent socket alias is used. A current
socket recipient qualifies only when its successful transport `msg_id` identifies
exactly one actual delivery in the selected receiver's transcript, with matching
body and a timestamp no earlier than the call. Missing or ambiguous correspondence
does not count. An unreadable receiver transcript cannot establish correspondence.

Reading the real results exposed another obstruction: all 16 current calls and
62 legacy calls omit `is_error`. Previously `target_calls` demanded an explicit
false value, so adding the new name alone would still leave real replies unable
to settle an item. For current calls, positive JSON `success: true` is required.
For legacy calls lacking the error flag, an anchored host acknowledgement must
name the exact recipient and a transport message ID, with a consistent
`delivered to`/`delivered` or `queued for`/`queued` status. An explicit error always
wins. Other tools retain their existing success requirements.

A queued acknowledgement establishes a call, not delivery. Acceptance still
requires the exact requested text in an actual receiver record attributable to
the target, after the receipt and no earlier than its successful send call.
Merely seeing a tool call in the diagnostic inventory does not settle anything.
No live receipt was reconstructed, no queue item edited and no configuration
alias added.

## Read-only census

The frozen target snapshot contained 61,530 records. Results:

| Population | Legacy | Current | Total |
| --- | ---: | ---: | ---: |
| Exact-name message calls | 64 | 16 | 80 |
| Calls addressed to the watchdog, now visible | 64 | 9 | 73 |
| Calls to other agents, excluded | 0 | 7 | 7 |
| Watchdog-addressed calls with recognized host acknowledgements | 62 | 9 | 71 |
| Watchdog-addressed calls with explicit errors | 2 | 0 | 2 |

Thus **all 80 calls are recognized as message calls; 73/73 watchdog-addressed
calls are visible**, in both the turn reader and reconciliation. Crediting all
80 as watchdog replies would be wrong. The two failed legacy attempts addressed
the watchdog's bare ID; the 62 others used its canonical ID. Of the nine current
watchdog calls, six used the canonical ID and three used socket addresses whose
transport IDs and bodies match actual watchdog deliveries. This is an inventory
of attempts, not a claim that all 73 were delivered successfully.

The one eligible sent item, the engine-test item, still returns **NOT-YET**:

```
no successful matching target reply call after delivery
```

That means the required close-out text is absent, not that every reply is absent.
The census called `A.evaluate` on an in-memory state copy, freezing transcript
reads; it did not run a live acceptance poll. Both the copied state and the
state/config file bytes were unchanged afterward.

## Controls

`tests/test_acceptance_contract.py` now has 55 cases. The positive current and
legacy cases invoke the real queue, receipt, `check msg-to-watchdog` and `owed`
handlers. Both require visibility and actual settlement. The socket case uses
real transport and receiver records. Negative controls cover other recipients,
lookalike tool names, conflicting addresses, missing or mismatched receiver
records, failed JSON results, explicit errors, and queued-but-undelivered replies.
The latter closes only after the actual receiver record is appended.

Against an isolated copy of `625be38`, the final controls reported:

```
FAIL: test_bare_session_uuid_still_identifies_watchdog (__main__.AcceptanceContract)
FAIL: test_current_message_reply_settles_and_is_visible (__main__.AcceptanceContract)
FAIL: test_current_message_without_delivery_cannot_settle (__main__.AcceptanceContract)
FAIL: test_legacy_message_reply_still_settles_and_is_visible (__main__.AcceptanceContract)
FAIL: test_legacy_queued_result_waits_for_actual_delivery (__main__.AcceptanceContract)
FAIL: test_reconciliation_current_reply_uses_same_reader (__main__.AcceptanceContract)
FAIL: test_reconciliation_excludes_other_recipients_and_similar_names (__main__.AcceptanceContract)
FAIL: test_similar_tool_name_cannot_count_as_reply (__main__.AcceptanceContract)
FAIL: test_socket_recipient_requires_delivered_transport_evidence (__main__.AcceptanceContract)
Ran 55 tests in 11.485s
FAILED (failures=9)
```

The without-delivery case fails its visibility assertion on the baseline, not
its refusal-to-settle assertion. The legacy positive fails settlement despite
being visible, exposing the separate missing-error-flag obstruction.

The real disposition handler control also fails on the baseline:

```
FAIL: test_current_message_question_cannot_be_held_or_closed (__main__.DispositionContract)
AssertionError: 0 == 0 : hold 2026-09-16T10:00:22Z with recorded attribution and turn evidence
Ran 8 tests in 0.068s
FAILED (failures=1)
```

One added error-control fixture initially reused a delivery UUID across two
variants and correctly failed with:

```
AssertionError: 1 != 0 : REFUSED: delivery uuid must identify exactly one transcript record
```

The variants now run as separate tests with isolated transcripts. No receipt
uniqueness rule was changed. All final controls pass. Full-suite verification on
both installed interpreters exited zero:

```
/usr/bin/python3 tests/run_all.py  # Python 3.9.6
RESULT: 28/28 scripts passed; 0 failed; exclusions: 0
python3 tests/run_all.py           # Python 3.14.7
RESULT: 28/28 scripts passed; 0 failed; exclusions: 0
```

No scripts were removed or excluded.

## Other external-name dependencies

An exact tool, field, event or attribute spelling is a dependency on a host
outside this repository. A renamed input can silently become “not seen,” which
these readers cannot distinguish from “did not happen.” This repair does not
claim general format-change detection. The following code audit identifies
remaining dependencies; their future rename failures are reasoned from code,
not reproduced incidents in this repair.

| Reader | External names or shape | Failure when the expected shape disappears |
| --- | --- | --- |
| `wd_lib.message_input`, `message_success`, `legacy_message_success` | The two explicit tool names; recipient/message fields; JSON success and transport ID; legacy host acknowledgement text | A new tool name/field/result can again disappear from inventory or leave acceptance NOT-YET. Explicit unreadable evidence can produce UNDECIDED. |
| `wd_receipts.socket_sender_matches` | `SendMessage`, `success`, `msg_id`, `verifiedPeerPid` | Cannot attribute a socket sender; receipt refuses. It is specifically the current transport correlation, not a legacy-name message inventory. |
| `wd_lib.Turn.background_launches` and `agent_launches` | `Bash`, `run_in_background`, host launch-result wording, task output path, `Agent` | Missing launches; potentially incomplete in-flight inventory. |
| `wd_lib.dispatch_scan` | `Bash`, `command`, executable name `codex-run` | A renamed tool or executable can yield no recognized dispatch. |
| `wd_lib.commits_in`, `pushes_in`, `files_written_in`; reconciliation's Bash readers | `Bash`, Git result text, `Write`, `Edit`, `NotebookEdit`, `file_path` | Missing commit/push/write artifacts. |
| `wd_acceptance.exact_git_call`, `attributed_file` | `Bash`/Git verbs; `Write`/`Edit` and their path/content fields | A real push or file change can lack qualifying attribution and remain NOT-YET. |
| `wd_lib.task_output_status`; task acceptance | `[exited with code N]`, `task-notification`, `<task-id>`, `<status>completed` | Completion becomes unseen; pending acceptance or a false stall remains possible. |
| `wd_lib.codex_thread_state` | `task_started`, `task_complete` event names and payload fields | Renamed lifecycle events can look absent even in a completely scanned record. |
| `wd_lib.Turn.end_state`, `wd_wait` turn detection | `end_turn`, `stop_sequence`, `max_tokens` | Changed stop reasons can leave a turn open or suppress a turn-end event. |
| `wd_receipts.delivery` | Delivered-record types, peer origin fields, `cross-session-message`, host prefix/footer | New envelope shapes refuse rather than gaining delivery credit. |
| `wd_lib.peer_replies`, `Turn.is_watchdog_turn`; reconciliation `PEER` and `_wrapped_from` | Peer-origin/session spelling and wrapper tags/attributes | A changed envelope or socket in place of a stable sender ID can disappear from these older inbound views. These are separate from outgoing message-tool identification and were not repaired here. |

The remaining inbound views deserve their own real-record census; this audit
cannot assert their current recall from their code alone. No heuristic tool-name
matching or override was added to make unknown formats look successful.
