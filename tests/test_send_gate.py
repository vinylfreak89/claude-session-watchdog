#!/usr/bin/python3
"""Controls for the send gate: `next` must refuse while busy, while a reply is unhandled, and while held.

The gate had exactly one refusal -- the target being mid-turn -- so the queue kept pace with TURN
BOUNDARIES rather than with the work, and turns were ending every couple of minutes. Worse, `queue hold`
wrote `hold_until` and this gate never read it: the hold printed "HELD UNTIL ..." and changed nothing.
That is the two-stores failure (`wd_wake.py --due` read the field, the gate did not) and the gate is
the reading side.

Owner, 2026-09-11: "you're not waiting for turns to close... don't rapid fire the queue."

Every control calls `next_item` -- the function the gate actually uses. An earlier attempt to verify
the hold re-derived the filter in the test and "passed" against a gate that ignored the field entirely,
which is the same defect one level up: checking a property by writing the property.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_check as C, wd_lib as W

class _Turn:
    def __init__(self, ts, state='end_turn'):
        self.end_ts = ts; self.end_state = state; self.start_ts = ts
        self.assistant_texts = [(ts, 'work')]; self.final_text = 'work'

TS = '2026-09-10T19:00:00.000Z'

def _setup(busy=False, owed_rows=()):
    W.last_turns = lambda sess, n=8: ('p', [_Turn(TS, 'open' if busy else 'end_turn')])
    C.owed = lambda sess, state: list(owed_rows)

def _q(**kw):
    item = dict(id='Q1', text='body'); item.update(kw)
    return dict(owner_queue=[item])

def main():
    fails = 0
    def check(name, expect, verdict, why):
        nonlocal fails
        ok = verdict == expect
        fails += not ok
        print('%-46s -> %-5s : %s%s' % (name, verdict, 'PASS' if ok else 'FAIL',
                                        '' if ok else '  (expected %s)' % expect))

    _setup(busy=True)
    v, _, why, _ = C.next_item(None, _q()); check('target MID-TURN', 'busy', v, why)

    _setup(owed_rows=[dict(ts=TS)])
    v, _, why, _ = C.next_item(None, _q()); check('its last reply UNHANDLED (owed)', 'owed', v, why)

    _setup()
    v, _, why, _ = C.next_item(None, _q(hold_until='Q34 resolved')); check('item HELD behind a condition', 'held', v, why)

    _setup()
    v, _, why, _ = C.next_item(None, dict(owner_queue=[])); check('nothing queued', 'none', v, why)

    _setup()
    v, item, why, n = C.next_item(None, _q()); check('clean: idle, nothing owed, not held', 'send', v, why)

    # the owner's override must beat all three refusals at once
    _setup(busy=True, owed_rows=[dict(ts=TS)])
    v, item, why, _ = C.next_item(None, _q(urgent=True, hold_until='x'))
    check('URGENT beats busy + owed + held', 'send', v, why)

    # and a held item must not be picked when an unheld one exists behind it
    _setup()
    st = dict(owner_queue=[dict(id='HELD', text='a', hold_until='x'), dict(id='FREE', text='b')])
    v, item, why, _ = C.next_item(None, st)
    ok = v == 'send' and item['id'] == 'FREE'
    fails += not ok
    print('%-46s -> %-5s : %s' % ('skips a held item to reach a free one', v, 'PASS' if ok else 'FAIL'))

    print('RESULT: %s' % ('all controls pass' if not fails else '%d FAILED' % fails))
    return 1 if fails else 0

if __name__ == '__main__':
    raise SystemExit(main())
