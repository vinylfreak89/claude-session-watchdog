#!/usr/bin/python3
"""Control: every hook event must carry the SEND GATE's own computed verdict.

The hook told the watchdog what the target DID and left what it may SEND to be remembered. It was not
remembered: two queued items went out together, and `wd.sh next` sat unused on the send path for a
whole evening while the queue was bypassed item by item.

Printing a usage PARAGRAPH would have been a third store of the rules with nothing keeping it in step
with the gate, and unread by the third firing. So the hook prints the gate's ANSWER. That is only true
if it CALLS the gate, which is what control 4 exists for: it replaces `next_item` with a distinctive
verdict and requires that verdict to appear in the hook's output. A version that re-described the
rules in the hook would pass controls 1-3 and fail 4 -- the same defect one level up as checking a
property by writing the property.

Control 3 is the one that matters most on a bad night: a gate that CANNOT run must say so by name.
`UNAVAILABLE` reading like `nothing queued` is missing-is-not-a-value, and it is how an ungated send
goes out while the hook looks clean.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_wait as H
import wd_check as C, wd_wake as WK

REAL_NEXT, REAL_LOAD = C.next_item, WK.load_state

def run(next_item):
    C.next_item = next_item
    WK.load_state = lambda d: {}
    try:
        return H.gate_lines(None, '/nonexistent-state-dir')
    finally:
        C.next_item, WK.load_state = REAL_NEXT, REAL_LOAD

def main():
    fails = 0
    def check(name, ok, got):
        nonlocal fails
        fails += not ok
        print('%-52s : %s' % (name, 'PASS' if ok else 'FAIL  got=%r' % (got,)))

    # 1. a refusal is printed as a refusal, and the usage line says send nothing
    out = run(lambda s, st: ('busy', None, 'TARGET BUSY (turn open). 5 queued. SEND NOTHING.', 5))
    check('refusal: verdict + "send nothing" usage',
          len(out) == 2 and out[0].startswith('NEXT: TARGET BUSY') and 'send nothing' in out[1].lower(), out)

    # 2. a send names the ONE item and the exact command that records it
    out = run(lambda s, st: ('send', {'id': 'Q43', 'text': 'body'}, '', 5))
    check('send: names the item and `wd.sh sent1 Q43`',
          len(out) == 2 and 'SEND Q43' in out[0] and 'wd.sh sent1 Q43' in out[1], out)

    # 3. THE ONE THAT MATTERS: a gate that cannot run must not read like a clean state
    def boom(s, st): raise RuntimeError('state file is corrupt')
    out = run(boom)
    check('gate raises -> named UNAVAILABLE, never silence',
          len(out) == 1 and 'UNAVAILABLE' in out[0] and 'state file is corrupt' in out[0]
          and 'nothing queued' not in out[0].lower(), out)

    # 4. the lines are the GATE's output, not a restatement of its rules
    out = run(lambda s, st: ('held', None, 'SENTINEL-9f3a REFUSAL', 2))
    check('calls next_item (sentinel reaches the output)',
          any('SENTINEL-9f3a' in l for l in out), out)

    # 5. the every-firing promise: whatever the event, the same two lines are produced
    out = run(lambda s, st: ('none', None, 'nothing queued.', 0))
    check('empty queue still prints a verdict + usage',
          len(out) == 2 and 'nothing queued' in out[0], out)

    print('RESULT: %s' % ('all controls pass' if not fails else '%d FAILED' % fails))
    return 1 if fails else 0

if __name__ == '__main__':
    raise SystemExit(main())
