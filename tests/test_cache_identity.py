#!/usr/bin/python3
"""Control: a cached parse and every index over it are bound to the RECORDS, not to a stat.

TWO DEFECTS, both reproduced by an independent evaluation on 2026-09-21, both in the layer
that decides whether the target actually acted.

1. THE APPEND RACE, and it failed toward CREDIT. `_msg_id_results` and `_origin_index` took
   the caller's records and then independently stat-ed the path to build their key. A record
   appended between those two operations meant an index built from the OLD records was filed
   under the NEW size/mtime. The next read parsed the appended record while the lookup
   returned an index that could not see it, so a duplicate SendMessage result stayed hidden
   and a delivery that a freshly built index REFUSES was credited instead. For an instrument
   whose whole job is refusing unproven deliveries, crediting is the expensive direction.

2. THE KEY OMITTED FILE IDENTITY. `read_records` keyed on (realpath, size, mtime_ns). Replace
   a file atomically with different content of the same length and restore its mtime, and
   every component matches although the inode changed -- the original reproduction printed
   "atomic replace changed inode: True disk contains new: True read_records stale: old". The
   docstring claimed a stale view could never be served; it could.

THE DEFECTS ARE BUILT HERE. Each case constructs its own file and its own records and forces
the collision by hand. Nothing depends on the live transcripts or on either race still
existing in production, so no fix elsewhere can silence these.

Case 3 is the one that guards the guard: if the identity check is made so strict that the
cache never hits, both races vanish and so does the reason any of this code exists. A cache
that never serves is not a fixed cache, it is a deleted one.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_lib as W
import wd_receipts as D


def check(name, cond):
    print(('PASS  ' if cond else 'FAIL  ') + name)
    return bool(cond)


def write(path, rows):
    with open(path, 'w') as f:
        for r in rows:
            f.write(r + '\n')


def rec(uuid, mid=None):
    """A record `_origin_index` indexes: a delivery carrying origin.msg_id."""
    origin = ', "origin": {"kind": "peer", "msg_id": "%s"}' % mid if mid else ''
    return '{"type": "user", "uuid": "%s", "timestamp": "2026-09-21T00:00:00.000Z"%s, "message": {"content": "x"}}' % (uuid, origin)


def origin_rec(uuid, mid):
    return dict(type='user', uuid=uuid, origin=dict(kind='peer', msg_id=mid),
                message=dict(content='x'))


def result_rec(mid):
    """A record `_msg_id_results` indexes -- a SUCCESSFUL tool_result carrying msg_id.

    The two indexes read different shapes, and an earlier version of this file fed the
    origin shape to both. `_msg_id_results` then returned an empty dict for every case and
    three assertions compared {} to {}, which passes and proves nothing. Keeping the two
    fixtures separate and named is the repair.
    """
    return dict(type='user', message=dict(role='user', content=[dict(
        type='tool_result', tool_use_id='u-' + mid,
        content='{"success": true, "msg_id": "%s"}' % mid)]))


def main():
    ok = True
    tmp = tempfile.mkdtemp(prefix='wd-cache-identity-')
    try:
        p = os.path.join(tmp, 't.jsonl')

        # 1. ATOMIC REPLACE, SAME LENGTH, MTIME PRESERVED. Every component of the old key
        #    still matches; only the inode changed. A stale parse must not be served.
        write(p, [rec('aaaaaaaa')])
        first = D.read_records(p)
        st = os.stat(p)
        other = os.path.join(tmp, 'other.jsonl')
        write(other, [rec('bbbbbbbb')])          # same byte length: same uuid width
        os.utime(other, ns=(st.st_atime_ns, st.st_mtime_ns))
        before_ino = os.stat(p).st_ino
        os.replace(other, p)
        after = D.read_records(p)
        ok &= check('same-length, mtime-preserved replace is not served from cache',
                    [r.get('uuid') for r in after] == ['bbbbbbbb'])
        ok &= check('  (the replace really did change the inode, so the case is real)',
                    os.stat(p).st_ino != before_ino)
        ok &= check('  (and the first read really was cached, so this is not a trivial pass)',
                    [r.get('uuid') for r in first] == ['aaaaaaaa'])

        # 2. THE APPEND RACE, forced, on BOTH indexes. Build over one record list, then ask
        #    for the same path with a longer list -- what a caller holds after the file grew.
        #    Each store keeps one entry per path, so the assertions are about CONTENT, not
        #    object identity: a stale index would answer without the appended record in it.
        og_old = W._origin_index(p, [origin_rec('cccccccc', 'MID-1')])
        og_new = W._origin_index(p, [origin_rec('cccccccc', 'MID-1'),
                                     origin_rec('dddddddd', 'MID-2')])
        ok &= check('origin index is rebuilt for a longer record list',
                    'MID-2' in og_new and 'MID-2' not in og_old)

        one = [result_rec('MID-1')]
        two = [result_rec('MID-1'), result_rec('MID-2')]
        idx_old = D._msg_id_results(p, one)
        ok &= check('  (the msg-id fixture is indexable at all -- not an empty-vs-empty pass)',
                    'MID-1' in idx_old)
        idx_new = D._msg_id_results(p, two)
        ok &= check('msg-id index is rebuilt for a longer record list',
                    'MID-2' in idx_new and 'MID-2' not in idx_old)

        # 3. THE CACHE MUST STILL HIT, or the "fix" is just a deleted cache and cases 1-2
        #    pass for the wrong reason. Same list object, no intervening build: served.
        again = D._msg_id_results(p, two)
        ok &= check('an unchanged record list is served from the msg-id cache',
                    again is idx_new)
        same_list = [origin_rec('x', 'MID-3')]
        og_first = W._origin_index(p, same_list)
        ok &= check('an unchanged record list is served from the origin cache',
                    W._origin_index(p, same_list) is og_first)

        # 5. THE RECEIPT MEMO. receipt() is memoised on (records identity, sender, ident):
        #    measured on a real `owed`, 131 calls resolved to only 65 distinct ids, so half
        #    were exact repeats. The properties that matter are that a repeat AGREES, that it
        #    hands back a COPY -- callers annotate the dict they receive, and sharing the
        #    cached object would let one caller's annotation surface in another's receipt --
        #    and that a REFUSAL is never cached, because a cached refusal outlives its reason.
        #    A delivery needs structural peer provenance, so the record carries origin.kind
        #    = peer; an earlier draft of this case used a plain record and raised before it
        #    ever reached the memo, testing nothing.
        deliv = ('{"type": "user", "uuid": "11111111", "timestamp": "2026-09-21T00:00:00.000Z",'
                 ' "origin": {"kind": "peer", "from": "sender-A"},'
                 ' "message": {"role": "user", "content":'
                 ' "<cross-session-message from=\\"sender-A\\">hello</cross-session-message>"}}')
        write(p, [deliv])
        try:
            r1 = D.receipt(p, 'sender-A', '11111111')
            r2 = D.receipt(p, 'sender-A', '11111111')
            ok &= check('the receipt memo agrees with itself on the repeat', r1 == r2)
            ok &= check('  and hands back a COPY, not the cached object', r1 is not r2)
            r1['injected'] = 'mutation by a caller'
            ok &= check('  so a caller mutating its receipt cannot poison the cache',
                        'injected' not in D.receipt(p, 'sender-A', '11111111'))
            # THE MEMO MUST ACTUALLY SERVE. Measured, not assumed: a repeat must do NO
            # prefix split. Without this the whole case passes with the memo deleted --
            # two uncached calls also return equal, non-identical dicts -- which is the
            # borrowed-control failure in its purest form: green while testing nothing.
            splits = []
            real_split = W.split_turns
            W.split_turns = lambda recs: (splits.append(len(recs)), real_split(recs))[1]
            try:
                D.receipt(p, 'sender-A', '11111111')
                before = len(splits)
                D.receipt(p, 'sender-A', '11111111')
                after = len(splits)
            finally:
                W.split_turns = real_split
            ok &= check('  and a repeat does ZERO prefix splits (the memo is live)',
                        after == before)
        except D.EvidenceError as exc:
            ok &= check('the receipt memo case builds a usable delivery (it did not: %s)' % exc, False)

        # A refusal must not be cached: an unknown id raises every time, not once.
        raised = 0
        for _ in range(2):
            try:
                D.receipt(p, 'sender-A', 'no-such-id')
            except D.EvidenceError:
                raised += 1
        ok &= check('a refused receipt is re-raised, never cached', raised == 2)

        # 4. A DIFFERENT LIST OF THE SAME LENGTH -- the collision case 1 is about, reaching
        #    the index layer. Length alone is not identity.
        same_len = [result_rec('MID-9')]
        idx_same_len = D._msg_id_results(p, same_len)
        ok &= check('a same-length but different record list is not served the old index',
                    'MID-9' in idx_same_len and 'MID-1' not in idx_same_len)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

        # 6. THE ABSORBED-REPLY INDEX. Its validation is hoisted (343 calls / 13.3 s in one
        #    `owed`, each re-walking the whole receiver transcript), but the DECISION is not:
        #    the arrival-time filter and the exactly-one rule still run per query against the
        #    candidate LIST. Grouping to a single record would silently destroy the ambiguity
        #    guard, which is the one property this function exists to enforce.
        def absorbed(body, ts):
            return {'type': 'queue-operation', 'operation': 'remove',
                    'reason': 'absorbed_mid_turn', 'timestamp': ts,
                    'content': '<cross-session-message from="peer-1">%s</cross-session-message>' % body}
        floor = '1970-01-01T00:00:00.000Z'
        one = [absorbed('same body', '2026-09-21T10:00:00.000Z')]
        two = [absorbed('same body', '2026-09-21T10:00:00.000Z'),
               absorbed('same body', '2026-09-21T11:00:00.000Z')]
        ok &= check('one absorbed match credits', W.absorbed_receipt(one, 'same body', floor) is True)
        ok &= check('TWO matches credit NOTHING -- the ambiguity guard survives grouping',
                    W.absorbed_receipt(two, 'same body', floor) is False)
        ok &= check('  and the arrival-time filter can still reduce two candidates to one',
                    W.absorbed_receipt(two, 'same body', '2026-09-21T10:30:00.000Z') is True)
        ok &= check('  a body that never arrived credits nothing',
                    W.absorbed_receipt(two, 'never sent', floor) is False)
        # the index must be keyed to its records, not leak between different sets
        ok &= check('  a different record set is not answered from the previous index',
                    W.absorbed_receipt(one, 'same body', floor) is True and
                    W.absorbed_receipt(two, 'same body', floor) is False)

    print('\n%s' % ('ALL PASS' if ok else 'FAILURES ABOVE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
