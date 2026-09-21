#!/usr/bin/python3
"""Control: hoisting the tool-record scan out of target_calls changes NOTHING it returns.

WHY THIS EXISTS. `target_calls` is called once per archived item on every `owed` poll, and
each call walked the WHOLE transcript and re-parsed every record's content blocks. That is
O(archived items x transcript length) and both only grow. Measured 2026-09-21: 28 archived
items against ~71,000 records, and `owed` stopped completing at all -- over 400 s with no
output. That also stalled the settle path, so a delivered item whose acceptance HAD come
true could not close, and the loop reported it as still owed. The hang was located by
faulthandler, not guessed: the stack sat in target_calls, under evaluate, under
accepted_reply_turns, under owed.

The repair is an index keyed by transcript version. A performance change to the acceptance
layer is exactly where a silent semantic change would be most expensive -- this code decides
whether the target ACTED -- so the control here is differential, not behavioural: the old
implementation and the new one are both run over the same real input and must agree
EXACTLY, including agreeing on which inputs raise.

A reimplementation of the old scan lives in this file on purpose. Comparing the new code
against a description of the old behaviour would only prove I described it consistently;
comparing it against the old ALGORITHM proves the outputs match. The copy is frozen here and
does not track later edits, which is the point: if someone changes the real function's
meaning, these stop agreeing.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_acceptance as A
import wd_lib as W
import wd_receipts as D


def target_calls_original(sess, after):
    """The pre-index implementation, verbatim in shape: full scan, parse per record."""
    boundary = D.epoch(after)
    records = D.read_records(W.transcript_path(sess))
    uses = {}
    complete = []
    for record in records:
        content = (record.get('message') or {}).get('content')
        role = record.get('type')
        block_kind = {'assistant': 'tool_use', 'user': 'tool_result'}.get(role)
        blocks = W._blocks(content, block_kind) if block_kind else []
        if not blocks:
            continue
        stamp = record.get('timestamp')
        if D.epoch(stamp) < boundary:
            continue
        if record.get('type') == 'assistant':
            for b in blocks:
                ident = b.get('id')
                if ident in uses:
                    raise D.EvidenceError('duplicate target tool-use id %r in receipt window at or after %s'
                                          % (ident, after))
                uses[ident] = dict(id=ident, name=b.get('name'), input=b.get('input') or {}, ts=stamp)
        elif record.get('type') == 'user':
            for b in blocks:
                use = uses.get(b.get('tool_use_id'))
                if not use:
                    continue
                if use['name'] in W.MESSAGE_TOOL_NAMES:
                    if b.get('is_error', False) is not False:
                        continue
                    if use['name'] == 'SendMessage':
                        if not W.message_success(W._result_text(b)):
                            continue
                    elif b.get('is_error') is not False and not W.legacy_message_success(use, W._result_text(b)):
                        continue
                elif b.get('is_error', False) is not False:
                    continue
                if D.epoch(use['ts']) <= boundary or D.epoch(stamp) < D.epoch(use['ts']):
                    continue
                complete.append(dict(use, result=W._result_text(b), result_ts=stamp))
    return complete


def outcome(fn, sess, after):
    """Return value or raised error, so 'both raise the same thing' is a PASS, not a crash."""
    try:
        return ('ok', fn(sess, after))
    except Exception as exc:
        return ('raised', '%s: %s' % (type(exc).__name__, exc))


def check(name, cond):
    print(('PASS  ' if cond else 'FAIL  ') + name)
    return bool(cond)


def main():
    target = os.environ.get('WD_TEST_TARGET', 'BlackMagic Intensity USB driver for Apple Silicon')
    try:
        sess = W.find_session(target)
        records = D.read_records(W.transcript_path(sess))
    except (SystemExit, OSError, D.EvidenceError) as exc:
        # No live transcript on this machine: say so loudly rather than printing a green
        # result for a comparison that never ran. A skip that looks like a pass is the
        # failure mode this whole file exists to prevent.
        print('SKIPPED: no readable target transcript (%s: %s)' % (type(exc).__name__, exc))
        print('\nSKIPPED - NOT A PASS')
        return 0

    stamps = [r.get('timestamp') for r in records if r.get('timestamp')]
    if len(stamps) < 10:
        print('SKIPPED: transcript too small to be a meaningful comparison')
        print('\nSKIPPED - NOT A PASS')
        return 0

    ok = True
    ok &= check('the real transcript is large enough to matter (>%d records)' % 1000,
                len(records) > 1000)

    # Boundaries spread across the whole file, plus both ends, so the comparison covers an
    # empty window, a full window and everything between -- not one convenient midpoint.
    ordered = sorted(stamps)
    picks = [ordered[0], ordered[-1]] + [ordered[i * len(ordered) // 12] for i in range(1, 12)]

    agreed = 0
    for after in picks:
        old = outcome(target_calls_original, sess, after)
        A._TOOL_RECORDS_CACHE.clear()   # a cold cache must agree too, not only a warm one
        new = outcome(A.target_calls, sess, after)
        if old == new:
            agreed += 1
        else:
            ok = False
            print('FAIL  boundary %s disagrees' % after)
            print('      old: %s' % str(old)[:300])
            print('      new: %s' % str(new)[:300])
    ok &= check('old and new agree on all %d boundaries across the transcript' % len(picks),
                agreed == len(picks))

    # The cache must not leak between versions: a second call on the SAME records reuses the
    # index, and that reuse must not change the answer.
    after = picks[len(picks) // 2]
    first = outcome(A.target_calls, sess, after)
    second = outcome(A.target_calls, sess, after)
    ok &= check('a warm cache returns the same answer as a cold one', first == second)

    # And it must be keyed, not global: a DIFFERENT record set must not be served the index
    # built for this one.
    A._TOOL_RECORDS_CACHE.clear()
    A._tool_records('/fake/path', records)
    other = records[: len(records) // 2]
    served = A._tool_records('/fake/path', other)
    rebuilt = [x for x in served]
    A._TOOL_RECORDS_CACHE.clear()
    ok &= check('a different record set is not served a stale index',
                rebuilt == A._tool_records('/fake/path', other))

    print('\n%s' % ('ALL PASS' if ok else 'FAILURES ABOVE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
