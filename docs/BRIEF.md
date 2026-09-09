# Brief: build the orchestration watchdog session

Executor: a fresh session. You do not need the watched project's context. You are
building a tool, not doing the project's work.

## What this is

A long-running project session run by one or two working agents and an owner. The
owner's largest measured correction class is not wrong code — it is **missing
execution and coordination**: work announced and not started, dispatches reported
as sent that exited non-zero, background jobs dead for a day, commits made and not
pushed, ledger rows stale for turns.

The project's orchestration ledger — a markdown file of open items: the queue, the
per-item state, the dependency edges, the items gated on the owner — is reconciled
by hand by the working session, which is also the thing that forgets.

**You are building a separate, long-lived session that watches the working
session and tells it what the ledger and the world disagree about.**

## Hard boundaries — these define the tool

1. **Read-only on everything.** You never edit the ledger, the repo, or any
   project file. You tell the working session what to add, change or delete. It
   is the single writer.
2. **You never adjudicate a technical claim.** You cannot know whether an
   algorithm change is correct. You can know whether a row claiming it was fixed
   has a commit touching that file. Anything requiring project judgment is out
   of scope and stays with the owner.
3. **You never assert a fact you did not verify yourself.** Quote with
   provenance: "row X says unchanged after three turns; no commit since <sha>
   touches <path>" — never "X is stalled."
4. **You never tell it to keep going.** Measured externally: allowing repeated
   submissions raised specification-violating passage 33%→38% while legitimate
   passage rose 80%→83%. A push to continue manufactures invalid work. You
   report a named disagreement or you stay silent.
5. **Your messages arrive in the target as USER turns**, carrying the owner's
   apparent authority. A wrong message is obeyed as fast as a right one. This is
   why rules 2 and 3 exist.

## Verified mechanism — do not re-derive, but do re-check the three unknowns

Session state lives at:

    ~/Library/Application Support/Claude/claude-code-sessions/<acct>/<ws>/local_<uuid>.json

GLOB the two middle directories; do not hardcode them. Fields that matter:

| field | meaning | how it was verified |
|---|---|---|
| `completedTurns` | monotonic per-turn counter | a session reading 32 had had ~32 turns |
| `lastActivityAt` | epoch ms | — |
| `contextExceededCount` | count of HARD context failures (prompt rejected). NOT compaction: one session compacted 11-13 times with this at 1 | its single event matched one `"Prompt is too long"` record and the owner's message 7 min later |
| `cliSessionId` | -> `~/.claude/projects/<slug>/<cliSessionId>.jsonl`, the full transcript | — |

`isRunning` from `list_sessions` is **app-computed and not on disk**, and it is
true while the user is merely typing. Do not build on it.

Cross-session tools: `mcp__ccd_session_mgmt__send_message` (delivers to another
session as a user turn; result says `delivered` or `queued`; unavailable in and
to unattended/scheduled sessions), `list_events` (read another session's recent
turns AND tool calls), `list_sessions`, `get_session`.

**Resolve these three before building on them:**

1. Does `completedTurns` increment on an INTERRUPTED or ERRORED turn? Interrupted
   turns are exactly the case worth catching, so this changes the trigger design.
2. Does `list_events` prompt for approval in this installation? If it does, a
   silent poll cannot use it and you must work from the transcript file directly.
3. Confirm your own message to the target increments its `completedTurns` (it
   almost certainly does) — that is the feedback loop rule 4 of the speaking
   rules below exists to break.

## Triggers — two, not one

- **Turn completion.** Block until the target's `completedTurns` increments, then
  look. Prefer blocking on the transition over a fixed interval. This catches
  every turn-end failure.
- **Staleness backstop.** `lastActivityAt` older than a threshold WITH work
  believed in flight. This is the only way to catch a background job dead for a
  day; turn-completion can never see it, because that session is legitimately
  idle.

## What to check on each wake

Read the last turn's tool calls (not its prose summary), extract assertions with
a checkable referent, and verify each against the world:

- an announced action ("I'll render X", "dispatching Y") -> is there a running
  task or a completed one with a nonzero result?
- a dispatch reported as sent -> exit status, and the target thread's own state
- a commit claimed -> `git log`, and `git status` for local-vs-origin drift
- a file claimed written -> it exists, mtime is after the claim
- a ledger row claimed closed -> the row is gone (rows are DELETED, not marked)
  and something in the repo backs it
- rows whose state has not changed across N turns while dispatches went out
- `contextExceededCount` incremented -> the session is failing at the API edge

## Speaking rules

1. Speak only on a disagreement you can name **with a path or a command output**.
2. Never raise the same item twice without NEW evidence.
3. Never say "you seem stuck", "keep going", or anything about how the work is
   going. Report the disagreement and stop.
4. You caused a turn; do not fire again on the increment your own message
   produced.
5. Stay silent while the owner is actively conversing unless the finding is
   destructive-risk. Interleaving into a live exchange costs him more than the
   finding is worth.

Message form:

    [watchdog] <claim, quoted, with turn/time> | <what I checked> | <result>
    e.g. [watchdog] turn 761 said "writing the report now";
         no task running, no file under <output dir> modified since 17:41Z.

## Acceptance — how the owner knows it works

Do NOT tune it against a target rate. Log every message you would send, with its
evidence, for the first 20 findings, and let the owner mark each TRUE or FALSE.
Precision on those 20 is the acceptance number; a watchdog with false positives
is worse than none, because rule 5 above makes them authoritative.

Report cost per wake separately.

## Out of scope, explicitly

Premise errors, wrong measurements, bad instruments, and whether a change is
correct. Those are the classes that cost this project the most days and no
watchdog addresses them. Do not attempt them and do not imply coverage.
