# Transcript reads during a growing poll

A file append must be visible to the next receipt check. It must not require decoding
all prior JSON records again. The parser now keeps one structural snapshot per path:
records, raw delivery candidates, byte length, SHA-256 digest, and file version.

An unchanged-file hit still checks device, inode, size, mtime and ctime. On a changed
version the reader reads the current bytes and hashes the entire previous prefix.
Only a matching digest permits reuse of decoded records. It decodes the appended
suffix; a rewritten or truncated prefix triggers a full parse. Unterminated final
lines are reparsed, and malformed JSON still refuses. A file that moves during the
read cannot create an unchanged-file cache hit: its bytes are only a baseline for
the next read's prefix verification.

Delivery candidates share the validator's structural eligibility predicate. They
retain order and duplicates, and include potentially eligible records with invalid
timestamps. Every candidate still runs through delivery validation, including its
sender transcript when attribution requires it. No acceptance result or delivery
verdict is memoised. This extends the existing parse snapshot rather than adding
another cache with a separate invalidation rule.

The turn cache retains at most eight recently used snapshots. Eviction recomputes
turn structure; it does not change any obligation or acceptance decision.

## Validation

Controls exercise growth, rewrite plus append, partial JSON, universal newlines,
unterminated final records, sender ambiguity after a warm candidate lookup, and the
real `owed` handler settling a reply while skipping impossible delivery records.
The performance controls failed before the repair: 255 rather than 45 decodes for
40 records followed by five appends, and 600 unnecessary delivery attempts in the
handler control. Both now pass. The complete suite passes 47/47 scripts.

Measurements against real transcript copies, using isolated state:

| Work | Before | After |
|---|---:|---:|
| JSON records decoded across six reads with five appends | 446,931 | 74,491 |
| CPU seconds for those growing-file reads | 26.19 | 4.32 |
| Message-acceptance delivery attempts in one frozen poll | 216,126 | 25 |
| CPU seconds for the frozen poll | 31.88 | 24.33 |

The frozen poll's complete output and resulting state were byte-identical. A live
`wd.sh owed` run completed successfully in 16.56 wall seconds and 14.46 child CPU
seconds, following earlier runs that exceeded 300 and 120 seconds. Host load varies;
the work counts are stronger evidence than a wall-time ratio or a latency guarantee.

## Remaining costs and limits

Changed files still require reading and hashing their previous bytes. Cold reads
still decode the whole transcript. This trades bounded byte processing for repeated
JSON allocation without assuming that file growth proves an unchanged prefix.

The parser and structural indexes still share record dictionaries under the existing
immutable-record contract: callers must not modify them. File-version checks and
prefix verification are not an atomic multi-file snapshot against arbitrary concurrent
rewrites. The repair does not freeze evidence for a whole poll or relax delivery checks
to hide those limits.
