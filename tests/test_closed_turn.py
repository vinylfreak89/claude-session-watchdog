#!/usr/bin/python3
"""Control for `closed`: the owner's D15 exception, and the thing that stops it becoming a mute button.

His ruling: "you should never have an unacknowledged turn. you should always reply to the target with
what you are acknowledging... only if you end up with a simple response (1 line or less) those turns
you may close. I will let that TRY to slide past for now, but if you two get into loops of just
writing ACK at each other to bypass doing work, I'm going to ban you again LOL"

So a turn whose honest reply is a line or less may be closed WITHOUT a send. That collides with the
guard he ordered on `answered`, which now demands evidence of a delivered message -- correctly, and it
must keep demanding it. Hence a separate disposition rather than a loosening of that one.

The load-bearing control is #2, exactly as D15's option (b) required: an ORDINARY unanswered turn must
still fire. Without it this verb is the mute button, and the failure he named is two agents closing
each other's turns to look busy.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_check as C

def main():
    fails = []

    # 1. A closed turn is recorded with its reason, and is no longer owed.
    st = {}
    ok, why = C.close_turn(st, '2026-09-11T03:28:22.263Z', 'pure acknowledgement, nothing actionable')
    if not ok:
        fails.append('refused a legitimate closure: %s' % why)
    rec = (st.get('closed_turns') or {}).get('2026-09-11T03:28:22.263Z')
    if not rec or 'acknowledgement' not in rec.get('reason', ''):
        fails.append('did not record the closure with its reason: %r' % st)

    # 2. THE MUTE-BUTTON CONTROL, which D15's option (b) demanded by name: an ORDINARY turn --
    #    one never closed -- must still be owed. If this ever passes vacuously the verb is a hole.
    if C.turn_is_closed(st, '2026-09-11T01:25:08.156Z'):
        fails.append('an ordinary unclosed turn was treated as closed -- this is a mute button')

    # 3. A closure needs a STATED reason. "Closed" with nothing said is the ACK loop he warned about.
    ok, _ = C.close_turn({}, '2026-09-11T03:28:22.263Z', '')
    if ok:
        fails.append('accepted a closure with no reason')
    ok, _ = C.close_turn({}, '2026-09-11T03:28:22.263Z', '   ')
    if ok:
        fails.append('accepted a whitespace reason')

    # 4. It must not double as an acknowledgement of a DIFFERENT turn.
    st2 = {}
    C.close_turn(st2, '2026-09-11T03:28:22.263Z', 'nothing actionable')
    if C.turn_is_closed(st2, '2026-09-11T03:26:33.036Z'):
        fails.append('closing one turn closed another')

    for f in fails:
        print('FAIL:', f)
    print('SELFTEST', 'FAILED' if fails else 'PASS', '(5 controls)')
    return 1 if fails else 0

if __name__ == '__main__':
    raise SystemExit(main())
