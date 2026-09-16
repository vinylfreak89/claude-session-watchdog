#!/usr/bin/python3
"""Control: an item is SENT on evidence of delivery, and CLOSED only on evidence that the target acted.

Two separate properties, and before this both were missing.

DELIVERY. `answered` was already gated on the target's transcript, but `sent1` and the findings
`--sent` were not -- and all three set `last_send_ts`, which is what clears an owed turn. So the
gate had two unlocked doors beside it: either verb silenced the nagger on this session's word
alone, with no message behind it.

ACTION. Delivery was also treated as the end of the obligation (owner, 2026-09-16: "answered needs
to not just be 'the target transcript agreed to it'. it needs to verify the target took action,
otherwise its supposed to remind"). A message that arrives and is ignored looked identical to one
that was acted on, so an item could be marked sent and vanish while nothing happened.

Now the item carries WHAT ACTING WOULD LOOK LIKE, `owed` re-runs that check every poll, and the
item closes from the record -- there is deliberately no verb to close one by hand, because that is
the say-so this path exists to remove.

Controls 1-3 and 9 fail against the old code. Controls 4, 6 and 11 are the positive ones: an
implementation that simply refuses everything, or one that never closes anything, passes the
negative tests and fails these.
"""
import sys, os, json, tempfile, shutil, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_check as C
import wd_lib as W

SELF = 'local_ffffffff-0000-1111-2222-333333333333'


def transcript(path, *stamps):
    """A target transcript in which OUR messages were delivered at each `stamps` time."""
    with open(path, 'w') as fh:
        for ts in stamps:
            fh.write(json.dumps(dict(type='user', timestamp=ts, message=dict(
                content='Another Claude session sent a message: <cross-session-message from="%s">hi</cross-session-message>' % SELF))) + '\n')


def main():
    fails = []

    def check(name, got, want):
        ok = got == want
        print('%-64s %s%s' % (name, 'PASS' if ok else 'FAIL', '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    tmp = tempfile.mkdtemp(prefix='wd-sent-')
    try:
        tx = os.path.join(tmp, 'target.jsonl')
        transcript(tx, '2026-09-16T10:00:00.000Z')
        artefact = os.path.join(tmp, 'thing.txt')
        a = types.SimpleNamespace(repo=tmp, ledger=None, self_sel=SELF, state_dir=tmp, quiet_min=10.0)
        sess = dict(cwd=tmp, sessionId='local_target', cli='cli-target')

        # ---- what may close an item at all -------------------------------------------------
        check('1. a situational kind cannot be an acceptance check',
              C.acceptance_valid('running')[0], False)
        check('2. an unknown kind cannot be one either',
              C.acceptance_valid('nonsense x')[0], False)
        check('3. an empty acceptance is refused',
              C.acceptance_valid('')[0], False)
        check('4. POSITIVE: a real check with its argument is accepted',
              C.acceptance_valid('file thing.txt')[0], True)
        check('5. a real kind without its argument is refused',
              C.acceptance_valid('commit')[0], False)

        # ---- delivery evidence --------------------------------------------------------------
        st = {}
        ok, why = C.answered_allowed(tx, SELF, st, None, 'msg-A')
        check('6. POSITIVE: a delivery in the transcript is credited', ok, True)
        ok2, why2 = C.answered_allowed(tx, SELF, st, None, 'msg-A')
        check('7. the SAME message id re-cites the same delivery (item + findings)', ok2, True)
        ok3, why3 = C.answered_allowed(tx, SELF, st, None, 'msg-B')
        check('8. a DIFFERENT message id cannot reuse one delivery', ok3, False)
        empty = os.path.join(tmp, 'nothing.jsonl'); open(empty, 'w').close()
        ok4, _ = C.answered_allowed(empty, SELF, {}, None, 'msg-C')
        check('9. no delivery at all is refused', ok4, False)

        # ---- action, not delivery ------------------------------------------------------------
        state = dict(owner_queue=[dict(id='Q1', text='do the thing', sent='2026-09-16T10:00:01Z',
                                       acted_when='file thing.txt')])
        rows = C.unacted_items(a, sess, state)
        check('10. a sent item with its artefact ABSENT is still owed',
              [(r['id'], r['decided'], r['satisfied']) for r in rows], [('Q1', True, False)])
        check('11. and nothing closed it', C.settle_acted(a, sess, state), [])

        open(artefact, 'w').write('done')
        check('12. POSITIVE: once the artefact exists the item closes',
              C.settle_acted(a, sess, state), ['Q1'])
        check('13. and it leaves the live queue', state['owner_queue'], [])
        check('14. archived with the evidence that closed it',
              bool(state['owner_queue_sent'][0].get('acted_evidence')), True)

        # ---- a check that cannot run must NAG, never close, and never kill the poll ---------
        # A malformed csv expression makes `check` raise SystemExit, which does NOT inherit from
        # Exception. Caught by name, this is an undecided item; uncaught, it killed `owed` outright
        # -- the one poll still running when everything else has gone quiet.
        state2 = dict(owner_queue=[dict(id='Q2', text='x', sent='2026-09-16T10:00:01Z',
                                        acted_when='csv thing.txt not-an-expression')])
        rows2 = C.unacted_items(a, sess, state2)
        check('15. a check that RAISES is undecided, and does not kill the poll',
              [(r['decided'], r['satisfied']) for r in rows2], [(False, False)])
        check('16. and an undecided check never closes the item',
              C.settle_acted(a, sess, state2), [])
        check('17. the item is still in the queue', len(state2['owner_queue']), 1)

        # A missing artefact is a DECIDED "not yet" -- and must say which file, so a typo in the
        # acceptance spec is visible rather than nagging forever with no clue why.
        state4 = dict(owner_queue=[dict(id='Q4', text='x', sent='2026-09-16T10:00:01Z',
                                        acted_when='file definitely_absent.txt')])
        r4 = C.unacted_items(a, sess, state4)[0]
        check('17b. a missing artefact is decided, not satisfied, and names itself',
              (r4['decided'], r4['satisfied'], 'no such file' in r4['evidence']), (True, False, True))

        # ---- an item with no acceptance recorded is owed forever, loudly -------------------
        state3 = dict(owner_queue=[dict(id='Q3', text='x', sent='2026-09-16T10:00:01Z')])
        rows3 = C.unacted_items(a, sess, state3)
        check('18. a sent item with NO acceptance cannot close', C.settle_acted(a, sess, state3), [])
        check('19. and says so rather than looking satisfied',
              rows3[0]['decided'] is False and 'no acceptance check' in rows3[0]['evidence'], True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n%d check(s) FAILED' % len(fails) if fails else '\nall checks passed')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
