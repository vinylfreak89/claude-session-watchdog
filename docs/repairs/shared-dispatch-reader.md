# One reader for executable dispatch calls

`wd_check.turn_made_a_dispatch` now delegates to `wd_lib.dispatches_in` with the
same session scope as wake. Both use `dispatch_scan`; there is no second regex
definition that credits arbitrary tool input or a `send_message` name.

`wd_shell` reads shell words, quotation, literal assignments, redirections,
command lists and pipelines without executing anything. It consumes complete
heredoc bodies as data before looking for subsequent commands. This recognizes
the real `cat > $S/brief.md <<'EOF' ... EOF` followed by `codex-run task ...`
shape, including output redirection and `tee`. Quoted command examples inside
the brief do not become additional calls. Literal variables may supply executable
and file paths; opaque substitutions are not expanded or interpreted as commands.
Pipeline/background assignments cannot alter the parent environment in the reader.

The scanner separates recognized attempts from non-error dispatches. An errored
tool result, an explicit refused/failed result, or recorded background failure
does not receive credit. A missing result is undecided. Wake retains failed
attempts for its failure diagnosis, but does not register failed/undecided attempts
as new Codex work or treat them as successful dispatches. This is a distinction
between diagnosis and credit within one evidence definition, not two parsers.

An acknowledged background call establishes scheduling, not receiver acceptance
or fulfillment of a promise. Shell compound results can also obscure an individual
command's status; the reader rejects explicit recorded failure but does not claim
that a zero wrapper status proves remote delivery. The declaration-binding
proposal's receipt requirement remains necessary.

## Measured coverage, not an assumed denominator

The review baseline was **0 recognized / 579 inputs rejected for newline, dollar
or backtick**. At the beginning of this repair, the full transcript had grown:
the old reader still recognized zero; the same character filter excluded **584**
inputs. There were **759** Bash inputs mentioning `codex-run`, including reading
notes, quoted examples, status commands and unsupported shells. These are not all
dispatches, so dividing recognized invocations by 584 would not be a recall rate.

Read-only evaluation of the entire record after the repair reported:

```
recognized_invocations: 154
unique_recognized_tool_calls: 139
nonerror_dispatches: 153
failed_invocations: 1
compound_nonerror_dispatches: 136
consumers_agree: true
reported_declarations_still_broken: 2
```

Multiple invocations can occur within one tool call. The reported delayed
heredoc dispatch is now recognized once. Its two earlier declarations remain
owed; this repair introduces no later-turn credit, subject inference or binding.
No live state was modified to perform the census.

## Remaining unsupported syntax is not a negative finding

Conditionals, loops/functions, shell-state mutation, command-local assignments,
dynamic executable names, and expanding heredocs remain unproven. Shell wrappers
and opaque argument substitutions make absence inconclusive. The reader does
not evaluate environment variables, run a shell, open a substituted file, or
infer execution from a string nested in another program. A recognized direct
invocation may still be present alongside opaque arguments; that establishes
the invocation, not the unknown argument's contents or a second nested call.

Wake now raises `dispatch_claim_no_call` only if the recent scans are conclusive
and contain no recognized attempt. Unsupported syntax or unavailable results
produce `DISPATCH CLAIM UNPROVEN` when relevant to a claim, rather than an
accusation of absence. Data-only quoted commands still permit the negative
finding. Unsupported calls receive no declaration credit. These limits are
visible; they are not silently classified as "never happened."

## Controls and failures

The existing `tests/test_launch_handlers.py` script now has 18 tests; it exercises
normal wake, bootstrap, replay and owed through their real command handlers.
New cases cover heredoc-plus-invocation, data-only heredocs, quoted examples,
pipelines, literal variable paths, tab-stripped delimiters, failed/refused/missing
results, unsupported conditionals, shell scopes and agreement between callers.
Normal wake must persist the recognized dispatch and in-flight thread.

Before the repair, the basic heredoc control and the shared-reader control failed
to see a dispatch. The failure-credit control reported:

```
AssertionError: 'DECLARED an action and made no dispatch: 1' not found in "OWED completed turns: 1  DUE NUDGES: 0\n   2026-09-16T10:00:26Z  [not answered or held]  I'll send the review to Codex.\nDECLARED an action and made no dispatch: 0\nSENT, NOT YET ACTED ON: 0\nUNANSWERED requests: 0 (due to nudge: 0)\n"
```

Two initial fixture assumptions were corrected after checking the real handlers:
bootstrap retains the previous `dispatch_log` (`AssertionError: 0 != 1`), and
the JSON report has no `disp_records` field (`KeyError: 'disp_records'`). The
persistence control now calls normal wake and reads its saved dispatch log.
A reset fixture also initially lacked a user opener and therefore had no turn
to check; restoring its synthetic opener fixed that setup error. The production
decision functions are not replaced in these controls.

The nine review reproductions remain unchanged. Three previously failing cases
now pass for the intended reasons:

- `test_failed_dispatch_keeps_promise_owed`
- `test_quoted_command_keeps_promise_owed`
- `test_watchdog_message_alias_is_not_promised_dispatch`

The unrelated-later-dispatch and exact `SendMessage`-to-watchdog controls continue
to pass. Four remain red and still require the binding contract:

- `test_deferred_matching_dispatch_fulfils_promise`
- `test_same_turn_unrelated_dispatch_keeps_promise_owed`
- `test_three_declarations_one_dispatch_leaves_two_owed`
- `test_turn_hold_does_not_fulfil_target_promise`

The review artifact reports `Ran 9 tests` / `FAILED (failures=4)`, exit 1. No
assertion or expected outcome was changed to obtain those transitions.

The final 18 handler tests were also run in a scratch copy against the three
production files from `076e346`. They exited 1 with `FAILED (failures=17)`
(including subtests). Against the repair they report `Ran 18 tests` / `OK`.
This checks the corrected fixtures against the actual old behavior.

Both complete suite runs exited zero:

```
/usr/bin/python3 tests/run_all.py  # Python 3.9.6
RESULT: 29/29 scripts passed; 0 failed; exclusions: 0

python3 tests/run_all.py           # Python 3.14.7
RESULT: 29/29 scripts passed; 0 failed; exclusions: 0
```
