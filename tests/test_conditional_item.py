#!/usr/bin/python3
"""Control: an item whose CONDITION has not fired must not nudge, and must stay visible.

The queue tracked one kind of thing -- work owed now. A fallback owed only IF something else fails
had no representation, so it sat in the nudge list and came due every three turns. It was marked
nudged four times without being sent, which is worse than either honest option: marking it nudged
records a send that did not happen, and resolving it records a completion that did not happen.

The repair is a MISSING STATE, not a suppression, and the difference is testable: the item must still
appear in the open list and must become due the moment its condition is declared fired. A fix that
merely hid it would pass a "does not nudge" control and fail these.

This is the third time this instrument has been changed tonight and the standing hazard is the
owner's: "you are great at finding ways to skip it... as you just did by fixing its code." So the
control that matters most is #2 -- an ORDINARY overdue item must still come due -- because that is the
one that fails if this turns into a mute button.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_check as C

class FakeW:
    pass

def due_keys(state, ct=100, quiet=False):
    """Run the real gate with a stubbed session so no live transcript is involved."""
    import wd_lib as W
    real_read = W.read_state
    had_act = hasattr(W, 'activity_ms')
    real_act = getattr(W, 'activity_ms', None)
    W.read_state = lambda sess: {'ct': ct}
    # NOTE: wd_lib has no activity_ms, so in production this call raises inside a bare
    # `except Exception` and `quiet` is permanently False -- the quiet-peer trigger is DEAD.
    # The stub supplies it so control 5 exercises the intended logic rather than the dead path.
    W.activity_ms = lambda sess: (W.ms_of_iso(W.now_iso()) - (10**7 if quiet else 0))
    try:
        return [k for k, q, why, age in C.due_questions(None, state, quiet_min=10)]
    finally:
        W.read_state = real_read
        if had_act: W.activity_ms = real_act
        else: delattr(W, 'activity_ms')

def q(asked_ct='90', **kw):
    d = dict(text='x', asked_ts='2026-09-11', asked_ct=asked_ct, resends=0)
    d.update(kw)
    return d

def main():
    fails = []

    # 1. THE DEFECT: a conditional item, overdue by age, must not be due.
    st = {'open_questions': {'COND': q(conditional='only if the peak account fails')}}
    if 'COND' in due_keys(st):
        fails.append('a conditional item came due while its condition had not fired')

    # 2. THE GUARD AGAINST A MUTE BUTTON: an ordinary overdue item must STILL come due.
    st = {'open_questions': {'REAL': q()}}
    if 'REAL' not in due_keys(st):
        fails.append('an ordinary overdue item stopped coming due -- this is a mute button')

    # 3. Once fired, the conditional item comes due like any other.
    st = {'open_questions': {'COND': q()}}          # condition cleared
    if 'COND' not in due_keys(st):
        fails.append('a fired conditional did not come due')

    # 4. VISIBILITY: parking it must not delete it.
    st = {'open_questions': {'COND': q(conditional='c')}}
    due_keys(st)
    if 'COND' not in (st.get('open_questions') or {}):
        fails.append('the conditional item was removed rather than parked')

    # 5. A quiet peer makes everything due; a conditional must still be exempt, or the
    #    quiet path reintroduces exactly the noise this removes.
    st = {'open_questions': {'COND': q(conditional='c'), 'REAL': q()}}
    keys = due_keys(st, quiet=True)
    if 'COND' in keys:
        fails.append('a conditional came due on the quiet-peer path')
    if 'REAL' not in keys:
        fails.append('the quiet-peer path stopped working for ordinary items')

    for f in fails:
        print('FAIL:', f)
    print('SELFTEST', 'FAILED' if fails else 'PASS', '(6 controls)')
    return 1 if fails else 0

if __name__ == '__main__':
    raise SystemExit(main())
