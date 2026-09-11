#!/usr/bin/python3
"""Control: a NEGATED intention is not a declared action.

INTENT_RE catches "I'm dispatching it to Codex" by matching a subject marker, then up to 80
characters of anything, then an action verb. The gap swallows negation, so "I'm not re-dispatching"
matched and the target was reported as having DECLARED an action and made no dispatch -- for a
sentence saying it was deliberately not doing that, having explained why.

That is the costly direction. Every other member of this family returns a wrong number; this one
files a false accusation against the other agent, and the accusation reads as a process failure.

The negation must be rejected only when it sits BEFORE the action verb: "I'm dispatching it, not
waiting" is a real declaration and must survive. So the control tests both directions, and the
sentences are written here rather than lifted from live transcripts.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_lib as W

DECLARED = [
    "I'm dispatching it to Codex now",
    "I'll send it to Codex",
    "I'm putting this to Codex",
    "I'm dispatching it, not waiting for the render",   # negation AFTER the verb: still a declaration
    "getting that review now",
]
NOT_DECLARED = [
    "I'm not re-dispatching",
    "I'm not going to send it to Codex",
    "I will not dispatch this tonight",
    "I'm not dispatching until the review returns",
    "I am not asking Codex for that",
]

def main():
    fails = []
    for s in DECLARED:
        if not W.declared_actions(s):
            fails.append('MISSED a real declaration: %r' % s)
    for s in NOT_DECLARED:
        got = W.declared_actions(s)
        if got:
            fails.append('flagged a NEGATED intention as declared: %r -> %r' % (s, got))
    for f in fails:
        print('FAIL:', f)
    print('SELFTEST', 'FAILED' if fails else 'PASS',
          '(%d controls)' % (len(DECLARED) + len(NOT_DECLARED)))
    return 1 if fails else 0

if __name__ == '__main__':
    raise SystemExit(main())
