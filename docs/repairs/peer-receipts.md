# Read actual peer delivery envelopes

The reader accepts both captured host formats: `from` plus `name`, and `from`
plus `from-name` / `from-mode`. It matches from the beginning of the delivered
message, allows the host's trailing guidance, and hashes only the stripped
inner payload. It does not search inside quoted prose. Duplicate attributes,
origin/envelope disagreement, nested or second envelopes, unexpected trailing
text, and disagreement with an available origin body remain refused.

The fixtures retain the captured wrapper, prefix and host guidance. Payloads,
identities, names and timestamps are replaced with synthetic values. The census
below uses the actual records, not the redacted fixtures.

## Sender identity across process restarts

A socket path, PID or display name is not a stable session identity. Keep the
configured sender session identity. For a socket delivery, require its structural
origin's message ID to match exactly one successful `SendMessage` tool result in
that configured sender's transcript. The result must identify exactly one real
`SendMessage` tool use carrying the same payload, made no later than delivery.
No socket alias is written to config or state. Changing the socket address does
not change the recorded sender; the restart control exercises that case.

This uses existing evidence, not a new producer protocol. If the configured
sender's transcript is unavailable, the source result is missing/ambiguous, or
its success, tool identity or payload does not match, attribution is unavailable
and recording refuses. The instrument cannot recover identity from the socket
path or name alone. It assumes host-owned transcript provenance is trustworthy;
it does not authenticate arbitrary edits to those files.

## Census

Read-only enumeration inspected **61,297 records**, comprising **361 peer
user/attachment deliveries** and **60,936 other records**. Each peer record was
passed to `delivery(record, configured_self)`; no recording function was called.

| Transport | Records | Validates | Refused |
| --- | ---: | ---: | ---: |
| Stable session origin | 360 | 346 | 14 |
| Socket origin | 1 | 1 | 0 |
| Total | 361 | 347 | 14 |

Baseline: **0/361**. All remaining refusals say, verbatim:

```
delivery must contain one matching peer envelope
```

They are peer-record ordinals 25, 68, 82, 123, 126, 174, 176, 189, 198, 199,
202, 203, 204 and 350 in transcript order. All contain multiple envelopes:
twelve contain two, ordinal 203 contains three, and ordinal 198 contains six.
Their nested content is intentionally not credited. The private UUID inventory
remains scratch output, not a public fixture.

## Deciding controls

Both captured-format controls run the actual command handlers:
`test_captured_old_envelope_and_host_footer_validate` uses `sent1`;
`test_captured_new_envelope_resolves_recorded_sender` uses `answered`.
Against a scratch checkout of `4545a09`, respectively:

```
AssertionError: 1 != 0 : REFUSED: delivery must contain one matching peer envelope
AssertionError: 1 != 0 : REFUSED: delivery has no structural peer provenance for this sender
Ran 2 tests in 0.008s
FAILED (failures=2)
```

Both now pass. Additional controls verify the payload hash excludes guidance,
quoted/nested/second envelopes and different senders refuse, socket names/PIDs
alone cannot establish identity, source results must be successful and unique,
malformed source inputs refuse, and a changed socket address still attributes
to the same recorded sender. All remain in the existing
`tests/test_answered_needs_evidence.py` script.

No historical receipt was recorded or reconstructed. The four abandoned sends
remain unrecorded. Parser coverage is not delivery credit.
