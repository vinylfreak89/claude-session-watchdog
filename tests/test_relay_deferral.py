#!/usr/bin/python3
"""Controls for the relay deferral: a turn CAPTURED for an unreachable owner leaves the alarm,
and nothing else does.

Why this exists. `owed` fires until a completed turn has been relayed to the owner AND answered
or held. When the owner is unreachable the relay half cannot be satisfied, so every later turn
accumulates and the alarm sits permanently on -- at which point it stops being a signal and a
genuinely unrelayed turn becomes invisible among the deferred ones. That is the alarm-always-on
failure, and muting it would have been the wrong repair.

So `digest <ts> "where"` records that a turn was captured for the owner's next read. It is a
DEFERRAL, never a resting state: the turn still prints, under its own heading, and only
`relayed` clears it.

The controls are derived from the ways the deferral could be WRONG, not from the case that
prompted it:
  A  it must refuse to defer a turn that is also unanswered      (deferral must not swallow a second duty)
  B  it must defer a captured, answered turn                     (the case it exists for)
  C  it must not change anything for a turn nobody captured      (no silent widening)
  D  relaying must drain it                                      (the deferral terminates)
A is the one that matters: without it, `digest` would be a mute button.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_check as C, wd_lib as W

TS = '2026-09-10T18:30:05.299Z'

class _Turn:
    def __init__(self, ts, state='end_turn'):
        self.end_ts = ts; self.end_state = state; self.start_ts = ts
        self.assistant_texts = [(ts, 'some work')]; self.final_text = 'some work'

def _stub_turns(busy):
    def f(sess, n=8):
        turns = [_Turn(TS)]
        if busy: turns.append(_Turn('2026-09-10T18:33:06.300Z', 'open'))
        return ('path', turns[-n:] if n < len(turns) else turns)
    return f

def _run(state, busy=False):
    W.last_turns = _stub_turns(busy)
    C.turn_made_a_dispatch = lambda t: False
    return C.owed(None, state)

def main():
    dig = {TS: {'note': 'digest.md'}}
    fails = 0

    rows, pend = _run(dict(last_relay_ts='', last_send_ts='2026-09-10T00:00:00Z', digested=dig))
    ok = len(rows) == 1 and len(pend) == 0
    print('A  captured + UNANSWERED  -> alarm  : %s (owed=%d captured=%d)' % ('PASS' if ok else 'FAIL', len(rows), len(pend)))
    fails += not ok

    rows, pend = _run(dict(last_relay_ts='', last_send_ts='2026-09-10T19:00:00Z', digested=dig))
    ok = len(rows) == 0 and len(pend) == 1
    print('B  captured + answered    -> defer  : %s (owed=%d captured=%d)' % ('PASS' if ok else 'FAIL', len(rows), len(pend)))
    fails += not ok

    rows, pend = _run(dict(last_relay_ts='', last_send_ts='2026-09-10T19:00:00Z', digested={}))
    ok = len(rows) == 1 and len(pend) == 0
    print('C  not captured           -> alarm  : %s (owed=%d captured=%d)' % ('PASS' if ok else 'FAIL', len(rows), len(pend)))
    fails += not ok

    rows, pend = _run(dict(last_relay_ts='2026-09-10T19:00:00Z', last_send_ts='2026-09-10T19:00:00Z', digested=dig))
    ok = len(rows) == 0 and len(pend) == 0
    print('D  relayed past it        -> clear  : %s (owed=%d captured=%d)' % ('PASS' if ok else 'FAIL', len(rows), len(pend)))
    fails += not ok

    # The busy case is the PRE-EXISTING send gate, not this feature: while the target is mid-turn
    # "not answered" is suppressed, so A cannot be exercised against a live busy target. Recorded
    # because the first attempt at A was run that way and came back inconclusive rather than green.
    rows, pend = _run(dict(last_relay_ts='', last_send_ts='2026-09-10T00:00:00Z', digested=dig), busy=True)
    print('   (busy target suppresses the answered half -- A is not testable live: owed=%d captured=%d)'
          % (len(rows), len(pend)))

    print('RESULT: %s' % ('all controls pass' if not fails else '%d FAILED' % fails))
    return 1 if fails else 0

if __name__ == '__main__':
    raise SystemExit(main())
