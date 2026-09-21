#!/usr/bin/python3
"""Control: an owed ITEM must prod for ITSELF, or for the work that superseded it, and nothing else.

Owner, 2026-09-21: "Each owed item should prod directly for it or its superseded work only.
That's the change you need. Right now you are just firing bullets without looking for the target."

The defect this locks down. An open QUESTION was nudged; a delivered ITEM was not. A delivered
item sat in `SENT, NOT YET ACTED ON` while the gate printed a global SEND NOTHING, so the loop's
entire response to owed work was to STOP rather than to ask about the specific thing owed.
Measured the same day: a render item delivered at 07:47:54 sat unacted for fifty minutes; in that
window the owner's next request could not be sent, was never prodded for, and nothing in the tool
ever named the render as the thing to ask about. He noticed from the outside; the tool did not.

THE DEFECT IS SYNTHESISED HERE, NOT BORROWED. Every item below is built by this file. Pointing the
control at the real Q102 would work exactly until Q102 settled, and then go quiet without failing
-- the worst way for a check to die, because a silent control reads as a passing one. Nothing
outside this file can silence these cases.

The case that matters most is #2. A repair that merely made prods rare would pass "does not prod a
fresh item" and fail "a stale one still prods", and rare-and-silent is the failure mode being
repaired, not a fix for it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_check as C
import wd_lib as W

NOW = '2026-09-21T09:00:00.000Z'


def prods(items, quiet=False, now=NOW):
    """Run the REAL due_items against a stubbed clock; no live transcript is involved."""
    real_now, real_act = W.now_iso, getattr(W, 'activity_ms', None)
    W.now_iso = lambda: now
    # activity_ms decides `quiet`; 0 min idle when busy, 99 min when quiet.
    W.activity_ms = lambda sess: W.ms_of_iso(now) - (99 if quiet else 0) * 60000
    try:
        state = {'owner_queue': items}
        unacted = [dict(id=i.get('id'), sent=i.get('sent'), spec=i.get('acted_when'),
                        status='undecided', evidence='')
                   for i in items if i.get('sent')]
        return C.due_items(object(), state, 10.0, unacted)
    finally:
        W.now_iso = real_now
        if real_act is not None:
            W.activity_ms = real_act


def item(ident, sent_min_ago=None, acceptance='file /tmp/thing.mp4', **kw):
    row = dict(id=ident, acted_when=acceptance, text='body of %s' % ident)
    if sent_min_ago is not None:
        row['sent'] = W.iso_of_ms(W.ms_of_iso(NOW) - sent_min_ago * 60000) \
            if hasattr(W, 'iso_of_ms') else _iso(sent_min_ago)
    row.update(kw)
    return row


def _iso(minutes_ago):
    import datetime
    base = datetime.datetime(2026, 9, 21, 9, 0, 0, tzinfo=datetime.timezone.utc)
    return (base - datetime.timedelta(minutes=minutes_ago)).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def check(name, cond):
    print(('PASS  ' if cond else 'FAIL  ') + name)
    return bool(cond)


def main():
    ok = True

    # 1. THE REAL SHAPE: delivered fifty minutes ago, acceptance not met, target busy.
    #    This is the Q102 case and it must prod. If this fails, nothing was fixed.
    rows = prods([item('QA', sent_min_ago=50)])
    ok &= check('a 50-min owed item prods even while the target is busy', len(rows) == 1)
    if rows:
        it, successor, why, age, count = rows[0]
        ok &= check('  the prod names the item itself, not a successor', successor is None)
        ok &= check('  it reports the age owed from delivery', '50 min' in why)
        ok &= check('  it starts at zero prods', count == 0)

    # 2. THE CONTROL THAT CATCHES A MUTE BUTTON. A freshly delivered item must NOT prod
    #    while the target is working -- but see case 3: going quiet makes it due at once.
    ok &= check('a 5-min owed item does not prod a busy target',
                len(prods([item('QB', sent_min_ago=5)])) == 0)

    # 3. Quiet is the other trigger, exactly as it is for the question nudge. A target that
    #    has stopped is the case where a young owed item most needs asking about.
    ok &= check('a 5-min owed item DOES prod once the target goes quiet',
                len(prods([item('QB', sent_min_ago=5)], quiet=True)) == 1)

    # 4. An UNSENT item is not owed by the target and must never be prodded for.
    ok &= check('an undelivered item never prods', len(prods([item('QC')])) == 0)

    # 5. SUPERSESSION, the owner's "or its superseded work only". A live replacement carrying
    #    the same acceptance takes the prod, and the stale original is left to lie.
    rows = prods([item('QD', sent_min_ago=50), item('QE')])
    ok &= check('a superseded item prods for its successor', len(rows) == 1)
    if rows:
        it, successor, why, age, count = rows[0]
        ok &= check('  the successor is named', successor is not None and successor['id'] == 'QE')

    # 6. ...but a HELD successor is not due work, so the prod falls back to the original
    #    rather than chasing something whose hold says it is not time yet.
    rows = prods([item('QF', sent_min_ago=50), item('QG', hold_until='waits on the engine')])
    ok &= check('a held successor is not a prod target', len(rows) == 1 and rows[0][1] is None)

    # 7. RE-ARMING. Recording a prod must push the clock forward, or the item prods on every
    #    poll forever and becomes noise -- the same way `resends` existed and never counted.
    prodded = item('QH', sent_min_ago=50)
    prodded['prods'] = 1
    prodded['last_prod'] = _iso(2)
    ok &= check('a just-prodded item goes quiet until it is stale again',
                len(prods([prodded])) == 0)

    # 8. ...and it must come BACK. A prod is temporarily satisfied, never permanently: only
    #    the acceptance closes an owed item.
    stale = item('QI', sent_min_ago=200)
    stale['prods'] = 3
    stale['last_prod'] = _iso(45)
    rows = prods([stale])
    ok &= check('a prodded item becomes due again once it goes stale', len(rows) == 1)
    if rows:
        it, successor, why, age, count = rows[0]
        ok &= check('  the prod count is carried', count == 3)
        ok &= check('  age owed is still measured from DELIVERY, not the last prod',
                    '200 min' in why)

    print('\n%s' % ('ALL PASS' if ok else 'FAILURES ABOVE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
