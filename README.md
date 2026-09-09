# claude-session-watchdog

A read-only watchdog for a long-running Claude Code session in the Claude desktop app. A second session runs
it: it hooks the working session's turn ends, verifies each assertion the working session made that has a
checkable referent (a commit, a push, a dispatch, a file, a ledger row, an announced next action), keeps an eye
on the background jobs and Codex turns it launched, and delivers a `[watchdog] …` message into the working
session only when its record and the world disagree — with the path or command output that shows it.

It never edits the project, never judges whether a change is correct, never tells the session to keep going, and
never asserts anything it did not check. The message text is produced by the scripts from the evidence; the
session running the loop may only veto a line, never write one. The original brief is `docs/BRIEF.md`.

```
wd.sh                 the watchdog session's commands: boot | wait | wake | sent | veto | cost | status
wd_lib.py             session discovery, transcript turn model, claim extraction, read-only world checks
wd_wait.py            the hook: kqueue on the session-state dir + transcript; one event line per turn end,
                      stall, overdue reply or timeout
wd_wake.py            one wake: latest completed turn -> claims -> checks -> findings -> policy -> report
wd_cost.py            per-turn token cost of the watchdog session itself, from its own transcript
config.example.json   copy to config.json: target, self, thresholds, optional ledger settings
WATCHDOG_SESSION.md   the standing instruction for the session that runs the loop
evidence/             the census instruments behind the measurements below
state/                (gitignored) state.json, findings.md, wake.log, cost.tsv, wait_memory.json
```

Requires macOS (kqueue), the Claude desktop app's Code tab, `/usr/bin/python3` (3.9 is enough), git. The
watchdog session needs the cross-session tools (`mcp__ccd_session_mgmt__send_message`, `get_session`).

## Setup

1. `cp config.example.json config.json`; set `target` (the working session's title or `local_…` id) and `self`
   (the watchdog session's own id, from `get_session self`). Optional: `repo` (defaults to the target's cwd),
   `ledger` (a markdown file with `| ID | … |` rows that are deleted when done), `engine_ref` (a second branch
   whose commits count as backing), `perm_paths` (files whose commits back a ledger row deletion), thresholds.
2. Open a new session, paste `WATCHDOG_SESSION.md` as its instruction, and let it run `wd.sh boot` and arm
   `wd.sh wait`.

## The loop

`wd.sh wait` blocks (kqueue on the session-state directory and the transcript, 15 s backstop) and prints one line:

| event | meaning |
|---|---|
| `TURN ct=a->b` | the app's turn counter moved and the transcript agrees the turn ended |
| `TURN_END ts=… opener=…` | the transcript ended the turn; the counter did not move within 6 s |
| `INTERRUPTED`, `API_ERROR` | the marker / error record was appended |
| `CONTEXT_EXCEEDED cec=a->b` | a prompt was rejected at the API edge (not a compaction) |
| `STALL kind=bg id=… idle_min=… reason=…` / `STALL kind=codex thread=…` | after `stale_after_s` of silence, an in-flight item showed no progress for `stall_min` |
| `REPLY_OVERDUE message_id=… sent=…` | the target did not answer the watchdog's question within `reply_min` |
| `TIMEOUT` | `--max-wait` reached (exit 3) |

Exactly one event per transcript turn. While the target is silent, the hook interrogates the in-flight work by
itself every 15 s (output growth, live child processes of the session, Codex rollout events) and says nothing
as long as it progresses. `wd.sh wake --trigger '<line>'` then analyses; the session sends the `MESSAGE` block
verbatim (one message), records `wd.sh sent`, logs `wd.sh cost`, and re-arms `wd.sh wait`.

A confirmed stall is the one question the watchdog asks: the message ends with a request to confirm whether it is
a stall, reconcile it against the contract, and reply to the watchdog session. The reply arrives as a
cross-session message; `wd.sh wake --trigger 'REPLY'` records it and re-verifies the in-flight work. If no reply
comes within `reply_min`, the hook emits `REPLY_OVERDUE` and the wake proposes exactly one poke.

Two-way talkback, measured: a target session can answer with `mcp__ccd_session_mgmt__send_message` (it may have
to load the tool by that exact name first; the test session's first attempt looked for a differently named tool
and gave up). If the watchdog is mid-turn the reply is `queued` and shows nowhere in the watchdog's transcript
until its turn ends, when it arrives as the next user turn — so the watchdog must end each wake promptly.

## What a wake checks

Tool calls first, prose second. From the latest completed turn: `git commit` results, `git push` calls,
`codex-run` dispatches (thread id, brief path, background task id), background launches, Write/Edit/heredoc/
redirect targets, task notifications; from the assistant's own text: sentences with a checkable referent.

| class | disagreement reported |
|---|---|
| `commit_missing` | a sha in the text is not a commit object in the repo |
| `push_not_on_remote` / `push_drift` | text says pushed (or commits were made and the text does not say "not pushed") while the branch is ahead of origin per `git ls-remote` — no fetch, read-only |
| `dispatch_failed` | a `codex-run` call whose output carries a failure signature or a non-zero exit |
| `dispatch_no_turn` | a dispatch older than 120 s with no `task_started` after it in the thread's rollout |
| `dispatch_claim_no_call` | text says dispatched/sent; no dispatch call in the last 3 turns |
| `file_claim_missing` / `file_claim_stale` | text says wrote/saved/rendered `<path>` (path after the verb) and the file is missing or older than the turn |
| `ledger_close_not_applied` | text says row X deleted/closed; X is still in the ledger |
| `ledger_close_unbacked` | a row vanished and no commit since the last snapshot mentions it, touches `perm_paths`, or touches the row's own paths |
| `ledger_stale_row` | a row unchanged for `stale_turns` turns while a dispatch whose brief names it went out; evidence lists `git log` for its paths on HEAD and `engine_ref` |
| `announced_nothing_running` | the final text announces an unconditional next action and nothing launched in that turn is alive |
| `task_dead` / `codex_turn_silent` | in-flight work with no exit marker, no notification, no live process / no rollout event |
| `reply_overdue` | the one poke |
| `context_exceeded` | `contextExceededCount` moved |

Everything without a checkable referent is dropped. Policy: `held:own-turn` (turn-derived findings on a turn
the watchdog's own message opened), `held:dup` (same key and evidence hash as a sent finding), `held:quiet`
(a human message in the target within `quiet_min`), else `send`. The acceptance log `state/findings.md` records
every finding, sent or held; the owner marks each SENT row TRUE or FALSE, and precision on the first 20 is the
acceptance number. Cost per wake is in `state/wake.log` (script time, transcript bytes read) and
`state/cost.tsv` (the watchdog session's own tokens per turn).

## How the desktop app's session state really behaves (measured 2026-09-09, macOS, app CLI 2.1.260)

| fact | how verified |
|---|---|
| Session state: `~/Library/Application Support/Claude/claude-code-sessions/<acct>/<ws>/local_<uuid>.json` (globbed). Used: `completedTurns`, `contextExceededCount`, `lastActivityAt`, `cliSessionId`, `title`, `cwd`. | 18 state files read |
| Transcript: `~/.claude/projects/<slug(cwd)>/<cliSessionId>.jsonl`, append-only; `slug` = non-alphanumerics → `-`. | mapped for all 18 |
| `completedTurns` is absent until the first turn completes. It moves ~2 s after a turn ends in the session the app is attached to; for an unattached session it moves only ~3 s after the next delivered input (three live cross-session sends: a finished turn sat uncounted for over three minutes; then `10→11` at the next delivery; then `11→13` at once). | `evidence/`, README history |
| `lastActivityAt` moves with transcript activity; a focus change rewrites the file without moving it. | 60 s sampling |
| A turn opens at a user record that is not a `tool_result` with a new `promptId`; tool results, mid-turn task notifications, queued human messages, slash-command records and interrupt markers inherit it; assistant records carry no `promptId`. | promptId census over 18 transcripts |
| A delivered cross-session message is a user record with `isMeta: true`, `origin.kind: "peer"`, `origin.from: <sender id>`, wrapped in `<cross-session-message from=… name=…>`, and a new `promptId`. | live sends |
| Interrupted and API-error turns most likely do not increment the counter (a session with 13 interrupt markers reads 12 turns short); not settled by a controlled test, and the hook does not depend on it. | `evidence/promptid_probe2.py` |
| Background tasks: `/private/tmp/claude-<uid>/<slug>/<cliSessionId>/tasks/<id>.output`, `[exited with code N]` appended on completion; the session's `claude` process carries `--resume=<cliSessionId>` and background tasks are its children. | 288 outputs, `ps` |
| Codex (`codex-run`) rollouts: `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<threadid>.jsonl`, `task_started` / `task_complete` events. | 3 days of rollouts |
| `list_events` prompts for nothing on a personal install; the transcript file is read directly anyway. | called once |

## Known limits

- Premise errors, wrong measurements, bad instruments, and engine correctness are out of scope by design.
- Claim extraction is pattern-based; the session's veto is the only judgment layer, and it may only remove.
- The ledger snapshot begins at `boot`; staleness counts turns from then.
- A `codex-run say` dispatch resolves its thread by chat, so its rollout is unknown to the checks.
- `push_drift` needs the network for `ls-remote`; without it the check uses the last-fetched remote ref and says so.
- A turn continued by a queued message after its final text is reported once, at its first end.
- Owner-present stretches are mostly silent by rule 5; the tool earns its keep on autonomous stretches, stalls
  and dead background work.

MIT licensed. Built with Claude Code from `docs/BRIEF.md`; every commit is dated from the build transcript.
