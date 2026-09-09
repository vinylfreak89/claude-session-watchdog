# Orchestration watchdog — scratch tool, read-only, outside the project

Built 2026-09-09 to the brief in `../WATCHDOG_BRIEF.md`. Nothing here lives in the repo; nothing here writes to
the repo, the ledger, or any project file. State is under `state/` beside the scripts.

## What it is

A separate, long-lived Claude session that watches ONE working session and reports — as a `[watchdog] …`
message delivered into that session — each place where the working session's own record and the world
disagree: a commit claimed and not present or not pushed, a dispatch reported as sent whose `codex-run` output
says it failed, a file claimed written that does not exist, a ledger row claimed closed that is still there, a
turn that ends announcing an action with nothing running, a background job dead with no notification, rows of
`docs/v10_pending.md` untouched across turns while dispatches that name them went out, or a hard context failure
at the API edge. It never adjudicates a technical claim, never tells the session to keep going, and speaks only
with a path or a command output behind every line. The message text is produced by the scripts from the
evidence; the session running the loop may only veto a line, never write one.

```
wd.sh        thin wrapper: wait | wake | boot | sent | veto | cost
wd_lib.py    session discovery, transcript turn model, claim extraction, read-only world checks
wd_wait.py   the trigger: kqueue on the session-state dir + transcript; one event line per turn end / stale
wd_wake.py   one wake: latest completed turn -> claims -> checks -> findings -> policy -> report + logs
wd_cost.py   per-turn token cost of the watchdog session itself (from its own transcript)
WATCHDOG_SESSION.md   the standing instruction for the session that runs the loop
state/       state.json (snapshot, dedupe memory), findings.md (acceptance log), wake.log, cost.tsv
```

## Verified mechanism (this machine, 2026-09-09; how each was checked)

| fact | how verified |
|---|---|
| Session state: `~/Library/Application Support/Claude/claude-code-sessions/<acct>/<ws>/local_<uuid>.json`, globbed. Fields used: `completedTurns`, `contextExceededCount`, `lastActivityAt`, `cliSessionId`, `title`, `cwd`. | 18 state files read; key census in the build transcript |
| Transcript: `~/.claude/projects/<slug(cwd)>/<cliSessionId>.jsonl`, append-only. `slug` = every non-alphanumeric character → `-`. | mapped for all 18 sessions |
| `completedTurns` is absent until a session's first turn completes. | this session's own file had no field mid-turn |
| `lastActivityAt` moves with transcript activity (a turn starting at 10:10:37Z moved it to 10:10:40Z); a focus change rewrites the file (`lastFocusedAt`) without moving it. | 60 s sampling of the working session |
| A turn is opened by a user record that is not a `tool_result` and whose `promptId` differs from the previous user record's; tool results, mid-turn task notifications, queued human messages, slash-command records and interrupt markers inherit the open turn's `promptId`; assistant records carry none. | promptId census over 18 transcripts; slash commands share the following human message's id |
| Task notifications that start a turn have `origin.kind == "task-notification"`; a delivered cross-session message is a user record with `isMeta: true`, `origin.kind == "peer"`, `origin.from == <sender local_ id>`, text `Another Claude session sent a message:\n<cross-session-message from="…" name="…">…`, and a NEW `promptId` (it opens a turn). | two live sends to an idle session |
| Background Bash tasks: `/private/tmp/claude-<uid>/<slug>/<cliSessionId>/tasks/<taskid>.output`; the harness appends `[exited with code N]` on completion; Agent tasks are symlinks to `…/<cli>/subagents/agent-<id>.jsonl`. | 288 output files inspected |
| The session's `claude` process carries `--resume=<cliSessionId>`; background tasks are its child processes. | `ps` census |
| Codex dispatches go through `codex-run task|send|say|queue|steer`; the thread rollout is `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<threadid>.jsonl` with `task_started` / `task_complete` (`last_agent_message`) events; codex-run prints `-- idle --`, `-- watch window ended, still running --`, `ACTIVE <id>`, `queued`/`queue failed`, `<method> failed: …` (exit 1). | 591 codex-run calls in the working session's transcript; 3 days of rollouts |
| `isRunning` from `list_sessions` is app-computed, not on disk. Not used. | brief; state files have no such key |

## The three unknowns — resolved

**1. Does `completedTurns` increment on an interrupted or errored turn?** Not decidable from the record alone,
and no controlled test was possible without an owner at the keyboard. Reconciling each session's counter against
its transcript (turns keyed by `promptId`): the session with 13 `[Request interrupted by user…]` markers
(Bravia, 510 transcript turns) reads 498, 12 short; the working session (777 turns; 2 interrupt markers, 2
API-error turns, 1 turn in progress at census time) read 765, 7 short; sessions with neither run 1–4 short for
reasons the record does not show (most likely turns cut by an app quit). Best reading: interrupted turns and
API-error turns most likely do NOT increment it. **The design does not depend on the answer:** `wd_wait.py`
also wakes on the interrupt marker and on the `isApiErrorMessage` record in the transcript, and on the
transcript's own end-of-turn (final assistant text, no tool call pending), and emits exactly one event per turn
whichever signal arrives first. Owner-settable in 30 s: press Stop mid-turn in any session and watch the field.

**2. Does `list_events` prompt for approval here?** No. Called from this session (`permissionMode: auto`,
no allow/deny rule for it in `~/.claude/settings.json`) it returned 5 of 6,124 messages with no prompt; its own
description limits prompting to managed deployments. The watchdog still reads the transcript file directly:
cheaper, complete (tool inputs and outputs), and independent of any prompt.

**3. Does your own message increment the target's `completedTurns`?** Yes, but not when the turn ends. Three
live sends to an idle session (`ToS GPU usage optimization`, counter 10, transcript 2.5 MB, each reply `ACK`):
- send 1 delivered 10:27:13Z; the target's turn ended 10:27:38Z; counter still 10 at 10:29:52Z and at 10:30:49Z.
- send 2 delivered 10:30:49Z; counter 10→11 at 10:30:52Z (3 s after delivery, 3 s BEFORE this turn ended at
  10:30:55Z); still 11 at 10:31:32Z and at 10:34:15Z.
- send 3 delivered 10:34:15Z; the turn ended 10:34:17Z; counter 11→13 at 10:34:18Z (3 s after delivery, 1 s
  after the end — both outstanding turns counted at once).
Reading: a delivery makes the app recount that session about 3 s later; a turn that completes with nothing
delivered afterwards stays uncounted for minutes. (UI-driven turns in the focused working session count ~2 s
after their end — measured on turn 771→772 at 10:32:10Z — so the difference is whether the app is attached to
the session, not the turn's origin as such.) Consequences built in: the watcher wakes on the transcript's own
end-of-turn (`TURN_END`) when the counter is late, folds a counter move that follows within 6 s into the same
event, and logs — never emits — a counter bump that lands while a turn is open or after the turn was already
reported; so each turn produces exactly one event. The wake marks a turn opened by the watchdog's own message
`own-turn` (by `origin.from` = the watchdog's session id, or the `[watchdog]` tag) and holds every finding on
it — that is rule 4 of the speaking rules. The three test messages are still in that idle session's transcript.

## Triggers (`wd_wait.py`; one line per event)

`TURN ct=a->b` (counter moved and the transcript agrees the turn ended) · `TURN_END ts=… opener=…`
(transcript ended the turn, counter did not move within 6 s) · `INTERRUPTED` · `API_ERROR` ·
`CONTEXT_EXCEEDED cec=a->b` · `STALE idle_s=N inflight=K` (`lastActivityAt` older than `--stale-after`, default
3 h, AND `state/state.json` lists in-flight work — the only way to see a background job dead for a day) ·
`TIMEOUT` (`--max-wait`, default 6 h). Event-driven via kqueue on the state directory and the transcript, with
a 15 s re-check backstop; `--follow` streams (for `Monitor persistent=true`), otherwise one-shot.

## What a wake checks (`wd_wake.py`)

Tool calls first, prose second. From the latest completed turn: `git commit` results, `git push` calls,
`codex-run` calls (thread id, brief path, background task id), background launches, Write/Edit/heredoc/redirect
targets, task notifications; from the assistant's own text: sentences with a checkable referent (a sha, a path,
a row id, a dispatch, an announced next action). Then, against the world:

| class | disagreement reported |
|---|---|
| `commit_missing` | a sha in the text is not a commit object in the repo |
| `push_not_on_remote` / `push_drift` | text says pushed (or commits were made and the text does not say "not pushed") while the branch is ahead of origin per `git ls-remote` (no fetch — read-only) |
| `dispatch_failed` | a `codex-run` call whose output (tool result or background output file) carries a failure signature / non-zero exit |
| `dispatch_no_turn` | a dispatch older than 120 s with no `task_started` after it in the thread's rollout |
| `dispatch_claim_no_call` | text says dispatched/sent; no `codex-run` dispatch call in the last 3 turns |
| `file_claim_missing` / `file_claim_stale` | text says wrote/saved/rendered <path> (path after the verb) and the file is missing, or older than the turn |
| `ledger_close_not_applied` | text says row X deleted/closed; X is still in `docs/v10_pending.md` |
| `ledger_close_unbacked` | a row vanished and no commit since the last snapshot mentions it, touches the permanent files, or touches its paths |
| `ledger_stale_row` | a row unchanged for ≥ `--stale-turns` (5) turns while a dispatch whose brief names the row id or its paths went out; evidence lists `git log` on HEAD and `origin/v10-engine` (as last fetched) for those paths |
| `announced_nothing_running` | the final text announces an action (`I'll render…`, `Dispatching…`) that is not conditional, and nothing launched in that turn is alive |
| `task_dead` | a background task with no exit marker, no notification, no live process under the session, older than `--dead-min` (10) |
| `codex_turn_silent` | a dispatched Codex turn in flight whose rollout has had no event for `--codex-quiet-min` (30) |
| `context_exceeded` | `contextExceededCount` incremented |

Everything without a checkable referent is dropped, never reported. Turn-end markers and API errors are logged
in the report, not sent (the owner sees those in the UI).

## Speaking rules, as implemented

- `held:quiet` — a human message in the target within `--quiet-min` (10) minutes, or an open human-opened turn.
- `held:own-turn` — the analysed turn was opened by the watchdog's own message.
- `held:dup(Fn)` — same dedupe key AND same evidence hash as a finding already sent; a proposal the session never
  marked sent or vetoed is treated as sent at the next wake (rule 2 safety).
- `send` — the report prints the exact `MESSAGE` block; the session sends it verbatim as ONE message and
  records `wd.sh sent F…,F… <message_id>`; a veto is `wd.sh veto Fn "reason"`. No other text is ever composed.
- A wake whose latest completed turn was already analysed (a lagged counter bump) is skipped outright.

## Acceptance

`state/findings.md` logs every finding — sent, held, vetoed — with its status and the message. The owner marks
each SENT row TRUE or FALSE. Precision on the first 20 sent rows is the acceptance number; nothing is tuned to a
rate. Cost per wake is reported separately: `wake.log` (script wall time, bytes of transcript read per wake) and
`cost.tsv` (the watchdog session's own tokens per turn: input, cache read, cache create, output, API calls).
Measured during the build: a wake's scripts take 0.1–0.7 s and read 2–8 MB of transcript tail; `ls-remote`
adds ~0.5 s.

## Known limits (stated, not implied covered)

- Premise errors, wrong measurements, bad instruments, engine correctness: out of scope by the brief.
- Claim extraction is pattern-based; the session's veto is the only judgment layer, and it may only remove.
- The ledger snapshot begins at bootstrap; `ledger_stale_row` counts turns from then, not from the row's birth.
- A `codex-run say` dispatch resolves its thread by chat, so its thread id (and rollout) is unknown to the checks.
- `push_drift` needs the network for `ls-remote`; without it the check falls back to the last-fetched remote ref
  and says so in the evidence.
- A turn continued by a queued message after its final text (same `promptId`) is reported once, at its first
  end; the continuation is folded into the next wake's view of the transcript.
- Owner-present stretches are mostly silent by rule 5; the tool earns its keep on autonomous stretches and on
  staleness. Interrupted-turn counting remains uncontrolled (unknown 1) but does not affect the triggers.
