#!/usr/bin/python3
"""Control: a recorded disposition must survive the harness rewriting session bookkeeping.

THE DEFECT, measured 2026-09-21. 23 of 46 recorded dispositions -- half of every hold and
close this tool had ever made -- were silently invalid. Nothing errored. `valid_disposition`
returned False and those turns reappeared in `owed` as though nobody had ever dealt with
them, which reads exactly like unfinished work rather than like a broken instrument. That is
the worst shape a failure can take here, because the loop's response to an owed turn is to
keep it loud, so the tool looked like it was working hardest precisely where it was broken.

THE CAUSE. `fingerprint` hashed every record in a turn's span, including session-level rows
the harness REWRITES IN PLACE:

  last-prompt      carries the owner's MOST RECENT message to the target, with no timestamp,
                   sitting inside the span of a turn that finished long before
  bridge-session   carries lastSequenceNum, which increments continuously
  ai-title / custom-title / mode / atis-latch   rewritten whenever the session changes

So every time the owner typed anything, finished turns changed fingerprint underneath their
own dispositions. Note what this rules out: these are not late arrivals appended after the
turn ended. Dropping records timestamped after end_ts recovered ZERO of the 23, because the
rows are mutated where they already sit.

THE CASES BELOW ARE SYNTHETIC. Pointing this at the live 23 would pass today and go quiet --
not fail -- the moment those turns aged out of the tracking window. Each case builds its own
turn and mutates it here, so nothing outside this file can silence them.

Case 3 is the one that matters. A fix that simply stopped checking the hash would pass
"survives a metadata rewrite" and fail "still rejects a changed answer", and an unchecked
hash is a worse instrument than a drifting one.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_turns as TD


class Turn:
    """Just enough of a real turn for the disposition path: records, plus the assistant
    texts the question-guard re-derives. The texts here are deliberately declarative, so
    the guard never fires and every case below is testing the FINGERPRINT and nothing else."""

    def __init__(self, records, end_ts='2026-09-21T09:02:34.765Z'):
        self.records = records
        self.end_ts = end_ts
        self.end_state = 'end_turn'

    tool_uses = ()

    @property
    def assistant_texts(self):
        out = []
        for r in self.records:
            if r.get('type') != 'assistant':
                continue
            for block in (r.get('message') or {}).get('content') or []:
                if block.get('type') == 'text':
                    out.append((r.get('timestamp'), block['text']))
        return out


SESS = {'sessionId': 'local_target'}


def turn():
    """A turn shaped like the real one that exposed this: content plus bookkeeping."""
    return Turn([
        {'type': 'user', 'uuid': 'u1', 'timestamp': '2026-09-21T09:00:00.000Z',
         'message': {'role': 'user', 'content': 'go on'}},
        {'type': 'assistant', 'uuid': 'a1', 'timestamp': '2026-09-21T09:02:34.765Z',
         'message': {'content': [{'type': 'text', 'text': 'Here is the measurement.'}]}},
        {'type': 'last-prompt', 'lastPrompt': 'an older thing he typed', 'leafUuid': 'x'},
        {'type': 'bridge-session', 'sessionId': 's', 'lastSequenceNum': 24109},
        {'type': 'ai-title', 'aiTitle': 'Some Session', 'sessionId': 's'},
        {'type': 'mode', 'mode': 'normal', 'sessionId': 's'},
    ])


def entry(t, at='2026-09-21T10:00:00Z', mode='hold'):
    return dict(reason='the owner is conversing with it directly', actor='local_watchdog',
                ruling=TD.RULINGS[mode], at=at, target='local_target',
                turn_hash=TD.fingerprint(t), reading='')


def check(name, cond):
    print(('PASS  ' if cond else 'FAIL  ') + name)
    return bool(cond)


def main():
    ok = True

    # 0. Baseline: a disposition recorded against an untouched turn is valid.
    t = turn()
    ok &= check('a fresh disposition validates', TD.valid_disposition(SESS, t, entry(t), 'hold'))

    # 1. THE REAL DEFECT. The owner types his next message; the harness rewrites last-prompt
    #    inside this finished turn. The disposition must survive -- the turn said the same thing.
    t = turn()
    e = entry(t)
    after = copy.deepcopy(t)
    for r in after.records:
        if r['type'] == 'last-prompt':
            r['lastPrompt'] = "You're missing the point still. On the fade back and hold..."
    ok &= check('survives the owner typing (last-prompt rewritten in place)',
                TD.valid_disposition(SESS, after, e, 'hold'))

    # 2. ...and the same for the counter that never stops moving.
    after = copy.deepcopy(t)
    for r in after.records:
        if r['type'] == 'bridge-session':
            r['lastSequenceNum'] = 99999
        if r['type'] == 'ai-title':
            r['aiTitle'] = 'Renamed By The Harness'
    ok &= check('survives bridge-session and title churn',
                TD.valid_disposition(SESS, after, e, 'hold'))

    # 3. THE CONTROL AGAINST GUTTING THE CHECK. If what the turn SAID changes, the
    #    disposition must die -- that is the whole reason the hash exists.
    after = copy.deepcopy(t)
    for r in after.records:
        if r['type'] == 'assistant':
            r['message']['content'][0]['text'] = 'Actually the measurement was wrong.'
    ok &= check('still REJECTS a disposition when the turn content changed',
                not TD.valid_disposition(SESS, after, e, 'hold'))

    # 4. ...and a genuinely new assistant text is content, not bookkeeping.
    after = copy.deepcopy(t)
    after.records.append({'type': 'assistant', 'uuid': 'a2', 'timestamp': '2026-09-21T09:02:40Z',
                          'message': {'content': [{'type': 'text', 'text': 'One more thing.'}]}})
    ok &= check('still REJECTS when the turn gained new content',
                not TD.valid_disposition(SESS, after, e, 'hold'))

    # 5. The frozen migration frontier. Entries written BEFORE the scheme changed keep every
    #    other clause and are excused only their unreproducible hash.
    t = turn()
    stale = entry(t, at='2026-09-20T12:00:00Z')
    stale['turn_hash'] = 'a' * 64
    ok &= check('a pre-migration entry survives an unreproducible hash',
                TD.valid_disposition(SESS, t, stale, 'hold'))

    # 6. ...but the frontier is not a bypass: a pre-migration entry still needs its reason,
    #    its actor, its ruling and the right target.
    for missing in ('reason', 'actor', 'ruling', 'target'):
        broken = entry(t, at='2026-09-20T12:00:00Z')
        broken['turn_hash'] = 'a' * 64
        broken[missing] = ''
        ok &= check('  a pre-migration entry with no %s is still refused' % missing,
                    not TD.valid_disposition(SESS, t, broken, 'hold'))

    # 7. And nothing written from now on can reach that branch, because `at` is stamped at
    #    write time. A post-migration entry with a bad hash is refused, full stop.
    fresh = entry(t, at='2026-09-21T23:00:00Z')
    fresh['turn_hash'] = 'a' * 64
    ok &= check('a post-migration entry gets no such excuse',
                not TD.valid_disposition(SESS, t, fresh, 'hold'))

    # 8. THE HOLE THE ALLOWLIST OPENS, and it is worse than the bug being fixed. If a turn
    #    carries no recognised content type, the filter yields [] and EVERY such turn hashes
    #    the same, so a disposition on one would validate against any other. Drift refuses a
    #    good disposition; collapse accepts a wrong one. Two turns that plainly differ must
    #    never share a fingerprint, whatever their record shape.
    a = Turn([{'no_type_field': 'first turn'}])
    b = Turn([{'no_type_field': 'a completely different turn'}])
    ok &= check('unrecognised record shapes do not collapse to one fingerprint',
                TD.fingerprint(a) != TD.fingerprint(b))
    e_a = entry(a)
    ok &= check('  and a disposition on one does not validate against the other',
                not TD.valid_disposition(SESS, b, e_a, 'hold'))

    print('\n%s' % ('ALL PASS' if ok else 'FAILURES ABOVE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
