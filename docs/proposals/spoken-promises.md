# Holding an agent to its own words, without a schema

**Status: proposal for the owner. Nothing implemented.** Replaces
`declaration-binding.md`, which he rejected.

## What he rejected, and why it matters here

The previous proposal answered "did the agent keep its promise?" with declaration
identities, protected subject mappings and receiver receipts — a schema an agent
must satisfy. His ruling:

  "the entire point of this tool is it is supposed to be controlled by natural
   language, not a strict rule hirearchy. A strict rule hirearchy an AI can route
   around. having to answer honestly and truthfully to adhere to the contract is
   harder."

That last sentence is the design. What follows is an attempt to build on it rather
than around it.

## The mechanism

**At the end of a turn, the agent writes its own open promises, in its own words,
as a short list.** Not fields. Sentences.

    OPEN:
      - sending the pool-test brief to Codex once the eight runs finish
      - rechecking whether the v9 truth file regenerates after the Makefile change

**The watchdog stores that list verbatim.** It does not parse it, classify it, or
extract subjects from it.

**At the next turn end, the agent must account for every line on its previous
list**, again in its own words — done, still open, or abandoned with the reason.
Carrying an item forward unchanged is fine. Abandoning it is fine:

    - sending the pool-test brief: done, dispatched at 14:38, commit f504c16
    - rechecking the v9 truth file: still open, the run hasn't finished
    - the OSD comparison I mentioned: dropping it, the capture doesn't contain
      the interval I thought it did

**The only mechanical rule is that nothing may silently disappear.** An item that
was on the list and is neither carried nor discharged is the alarm. That check is
trivial, needs no understanding of the content, and has no false positives of the
kind the current regex produces.

## Why this is harder to route around than a schema

A schema is satisfied by producing the right token. This is satisfied only by
writing a true sentence about each thing you said you would do. Omitting an item
is visible as a disappearance. Lying requires actively asserting something false in
plain language a human will read — which is a much higher bar than renaming a field
or failing to emit one.

It also makes honesty cheap. "I said I'd do this and I'm not going to, because X"
is a valid, complete discharge. The current instrument has no way to say that, so
the only way to clear a promise is to have kept it — which is precisely the pressure
that produces machinery to rescue a bad plan.

## The hole, stated plainly

**The agent can under-report by never putting an item on the list.** This mechanism
cannot catch that, and no amount of checking the list will find what was never
written on it. Anyone adopting this should know that is the residual risk.

Partial mitigation, and it rehabilitates something already built: the existing
`declared_actions` regex becomes an **advisory prompt, not a verdict**. "You wrote
'I'll send this to Codex' in this turn and it is not on your list — is that
deliberate?" It is allowed to be wrong, because being wrong costs a question rather
than a false accusation. That is the right job for a detector with its false-positive
rate, and it is the opposite of how it is used today.

## What it costs

A few lines per turn end, and a watchdog check that compares two lists for
disappearance. No new state beyond the previous list. No migration: the first list
starts empty, and nothing older is reopened.

## What it does not do

It does not verify that a promise was kept. It surfaces the agent's own account of
whether it was, and makes silence visible. Verification of the underlying work stays
where it already is — commits, test runs, the acceptance checks on queue items.

## Scope: symmetric (owner's ruling)

Asked whether this should bind only the watchdog or both agents, the owner ruled:
**"it should apply symmetrically."**

So it binds the watchdog, the target and Codex alike. Every agent lists its own open
promises and accounts for them; no agent audits another's list while keeping its own
private. The asymmetric version — one agent holding the others to a standard it is
exempt from — is exactly the shape that lets an instrument drift into a stick, and it
would have been my own convenience to propose.
