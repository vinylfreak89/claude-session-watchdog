#!/usr/bin/python3
"""Control for wake's queue listing: an item ALREADY SENT must never be offered for sending again.

`print_report` built its "SENDABLE NOW" list as `[it for it in q if not it.get('hold_until')]` --
it filtered on the HOLD field and never looked at `sent`. So every item ever delivered came back
on every wake under a heading that says "send with this wake", and on 2026-09-11 that was 22 of 27
items. The send gate (`wd_check.next_item`) filters `not it.get('sent')` correctly, so the two
readers of one store disagreed: `next` said 4 held, `wake` said 22 sendable.

That is the SAME defect as the `queue hold` failure this repo already has a control for -- one
store, two readers, one of them blind to a field -- and its cost here is the specific thing the
owner forbade: "don't rapid fire the queue."

The defect is SYNTHESISED here, never borrowed from live state: a control that needs the bug to
still exist in production stops being a control the moment it is fixed.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_wake as WK

def q(**kw):
    it = dict(id='Q1', ts='2026-09-11', text='x')
    it.update(kw)
    return it

def main():
    fails = []

    # 1. The defect itself: a SENT item must not be listed as sendable.
    got = WK.sendable_items([q(id='Q1', sent=True)])
    if got:
        fails.append('a SENT item was offered as sendable: %r' % [i['id'] for i in got])

    # 2. It must still refuse a held item -- the property the old filter DID have.
    #    Fixing one field must not lose the other.
    if WK.sendable_items([q(id='Q2', hold_until='step 3')]):
        fails.append('a HELD item was offered as sendable')

    # 3. Sent AND held: still nothing.
    if WK.sendable_items([q(id='Q3', sent=True, hold_until='step 3')]):
        fails.append('a sent+held item was offered as sendable')

    # 4. An unsent, unheld item MUST still come through, or the fix is a mute button.
    if [i['id'] for i in WK.sendable_items([q(id='Q4')])] != ['Q4']:
        fails.append('a genuinely sendable item was suppressed')

    # 5. Mixed queue, the shape that actually occurred: only the unsent+unheld one survives.
    mixed = [q(id='A', sent=True), q(id='B', hold_until='x'), q(id='C'), q(id='D', sent=True)]
    if [i['id'] for i in WK.sendable_items(mixed)] != ['C']:
        fails.append('mixed queue picked %r, want [C]' % [i['id'] for i in WK.sendable_items(mixed)])

    # 6. The two readers must AGREE. The whole defect was that they did not, so assert the
    #    invariant rather than each side separately: nothing wake offers may be one the gate
    #    would refuse as already sent.
    for it in WK.sendable_items(mixed):
        if it.get('sent'):
            fails.append('wake and the send gate disagree on %s' % it['id'])

    for f in fails:
        print('FAIL:', f)
    print('SELFTEST', 'FAILED' if fails else 'PASS', '(%d controls)' % 6)
    return 1 if fails else 0

if __name__ == '__main__':
    raise SystemExit(main())
