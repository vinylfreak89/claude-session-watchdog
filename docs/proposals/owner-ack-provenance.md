# Proposal: verify owner acknowledgements from an owner record

Status: **proposal only, not implemented**. The owner ruled that the existing
`answered --owner-ack "<his words>"` route must remain available. It accepts
nonempty words, records `owner_ack.words` and `owner_ack.at`, and acknowledges
completed turns through that instant. It does not manufacture delivery receipts
or archive sent work whose acceptance is still outstanding. An empty argument is
not an acknowledgement.

## What is unverified today

The command records an operator's attribution to the owner. It does not establish
that the owner actually said those words. A synthetic reproduction is to invoke
`answered --owner-ack "Synthetic owner authorization"` against a completed,
relayed turn with no incoming owner message: the turn becomes acknowledged. That
is the restored, authorized behavior, not grounds to remove the exception again.
The positive command control in `tests/test_owner_ack_explicit.py` demonstrates it.

## Proposed record contract

An acknowledgement would refer to one immutable owner message, with:

- a stable record ID and authenticated human author identity bound to the owner;
- the watchdog and target session identities and the explicit turn(s) covered;
- the exact words and timestamp, plus a digest of the covered target records;
- the disposition the owner authorized: acknowledgement, not a delivery claim or
  a successful action check.

The verifier would read the original owner channel, confirm identity and scope,
and record a separate acknowledgement receipt. Repeating that receipt would keep
its original scope; it could not acknowledge later turns. A timestamp or quote
supplied by the operator would not supply identity or scope. `user`, `attachment`
and `queue-operation` are channel types, not proof of human authorship. A peer
message, quoted owner text, or an operator-produced file would not suffice.

Scope must come from the owner's record. If ordinary prose does not unambiguously
name the covered turn(s), a trusted owner-facing confirmation could create that
scope. The constrained agent must not silently choose it. Older acknowledgements
without sufficient provenance need an explicit owner-approved migration policy;
the implementation must not invent missing identity or scope.

## Cost and decision required

This requires producer support for reliable owner identity and stable record IDs,
a trusted way to record scope, retained access to the relevant owner channel,
a separate receipt store, reconciliation support, and migration controls. The
owner may need to confirm scope for ambiguous messages. Missing or inaccessible
records would leave a turn owed, so adoption could interrupt legitimate historical
workflows. Exact quotes alone cannot resolve that cost.

If the operating agent can rewrite both the source record and the receipt store,
a CLI verifier does not provide a security boundary against deliberate tampering.
A stronger guarantee requires a protected recorder or authenticated records whose
verification keys the agent cannot replace. That is additional infrastructure,
not a command-line flag.

Approve the evidence contract, producer changes and migration policy before
implementation. Until then, preserve the restored owner-ack route and its empty
argument guard.
