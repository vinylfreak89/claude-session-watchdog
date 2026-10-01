#!/usr/bin/python3
"""Control: a close must survive the NEXT message's records being filed into the closed turn.

THE DEFECT, measured 2026-10-01 on the live target. The turn splitter appends every non-user
record to the current turn until the next opener, so when the next message was queued its
queue-operation enqueue/dequeue records landed in the span of the turn that had just been
closed. The fingerprint hashed the whole span, the stored hash stopped matching, and the turn
came back as owed: closes made at 10:22 were owed again in a 10:34 snapshot, and 10:44:53's
close lapsed at 11:09:59 when a task notification was queued.

The fingerprint now covers the turn through its last user or assistant record, and entries
written under the old whole-span scheme validate if their hash is the digest of some prefix
at least that long (they were written before the later records were appended).

Synthetic turns, so nothing outside this file can silence the cases. Cases 4-6 are the ones
that keep the check honest: a fix that stopped comparing hashes would pass 1-3 and fail those.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_turns as TD


class Turn:
    def __init__(self, records, end_ts='2026-10-01T10:44:53.433Z'):
        self.records = records
        self.end_ts = end_ts
        self.end_state = 'end_turn'

    tool_uses = ()

    @property
    def assistant_texts(self):
        return [(r.get('timestamp'), b['text']) for r in self.records if r.get('type') == 'assistant'
                for b in (r.get('message') or {}).get('content') or [] if b.get('type') == 'text']


SESS = {'sessionId': 'local_target'}


def turn():
    return Turn([
        {'type': 'user', 'uuid': 'u1', 'timestamp': '2026-10-01T10:44:30.604Z',
         'message': {'role': 'user', 'content': 'it will arrive sometime tomorrow'}},
        {'type': 'assistant', 'uuid': 'a1', 'timestamp': '2026-10-01T10:44:53.433Z',
         'message': {'content': [{'type': 'text', 'text': 'Noted for tomorrow.'}]}},
        {'type': 'system', 'subtype': 'stop_hook_summary', 'timestamp': '2026-10-01T10:44:54.912Z'},
    ])


def queued_next(t):
    """What the host files into the finished turn when the next message is queued."""
    after = copy.deepcopy(t)
    after.records += [
        {'type': 'queue-operation', 'operation': 'enqueue', 'timestamp': '2026-10-01T11:09:59.341Z'},
        {'type': 'queue-operation', 'operation': 'dequeue', 'timestamp': '2026-10-01T11:09:59.374Z'},
    ]
    return after


def entry(stored_hash, at='2026-10-01T10:51:26Z'):
    return dict(reason='owner-opened conversation; he read the reply', actor='local_watchdog',
                ruling=TD.RULINGS['closed'], at=at, target='local_target',
                turn_hash=stored_hash, reading='')


def old_scheme(t):
    """The whole-span digest the pre-fix fingerprint stored."""
    return TD._digest(TD._content(t))


def check(name, cond):
    print(('PASS  ' if cond else 'FAIL  ') + name)
    return bool(cond)


def main():
    ok = True
    t = turn()

    # 1. THE REAL DEFECT: closed, then the next message is queued into the span.
    e = entry(TD.fingerprint(t))
    ok &= check('a close survives the next message being queued into the span',
                TD.valid_disposition(SESS, queued_next(t), e, 'closed'))

    # 2. ...and it was the fingerprint that moved: the whole-span digest changes on queueing.
    ok &= check('  (the whole-span digest really does change, so case 1 is not trivial)',
                old_scheme(t) != old_scheme(queued_next(t)))

    # 3. An entry written under the OLD scheme (whole span incl. its trailing system record)
    #    recovers after more records are appended: its hash is a prefix of today's content.
    e_old = entry(old_scheme(t))
    ok &= check('an old-scheme close that had lapsed validates again',
                TD.valid_disposition(SESS, queued_next(t), e_old, 'closed'))

    # 4. CONTROL: changing what the turn said still kills the close, old or new scheme.
    changed = queued_next(t)
    changed.records[1]['message']['content'][0]['text'] = 'Actually, something else.'
    ok &= check('still REJECTS when the reply changed (new-scheme entry)',
                not TD.valid_disposition(SESS, changed, e, 'closed'))
    ok &= check('still REJECTS when the reply changed (old-scheme entry)',
                not TD.valid_disposition(SESS, changed, e_old, 'closed'))

    # 5. CONTROL: a new assistant reply after the close is content, not bookkeeping.
    grew = queued_next(t)
    grew.records.append({'type': 'assistant', 'uuid': 'a2', 'timestamp': '2026-10-01T11:10:00Z',
                         'message': {'content': [{'type': 'text', 'text': 'One more thing.'}]}})
    ok &= check('still REJECTS when the turn gained a new reply',
                not TD.valid_disposition(SESS, grew, e, 'closed'))
    ok &= check('  ...for an old-scheme entry too',
                not TD.valid_disposition(SESS, grew, e_old, 'closed'))

    # 6. CONTROL: a prefix SHORTER than the turn proper is never accepted. A hash of the
    #    opener alone (the turn minus its reply) must not validate the turn.
    short = entry(TD._digest(TD._content(t)[:1]))
    ok &= check('a hash of only part of the turn is refused',
                not TD.valid_disposition(SESS, t, short, 'closed'))

    # 7. CONTROL: a close on one turn does not validate a different turn.
    other = turn()
    other.records[0]['message']['content'] = 'a different question entirely'
    ok &= check('a close on one turn does not validate another',
                not TD.valid_disposition(SESS, other, e, 'closed'))

    print('\n%s' % ('ALL PASS' if ok else 'FAILURES ABOVE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
