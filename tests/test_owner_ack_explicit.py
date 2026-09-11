#!/usr/bin/python3
"""Control: the owner-acknowledgement bypass must require an explicit flag, never a stray argument.

The owner specified two routes for `answered`: evidence of a send, or "explicit acknowledgement from
the owner". The first implementation took ANY trailing argument as that acknowledgement -- and the
historical usage pattern was `./wd.sh answered <timestamp>`, so a timestamp would have been recorded
as the owner saying so. It was unreachable only because wd.sh happened to drop trailing arguments,
which is luck, not design.

A bypass that can be opened by accident is not a bypass, it is a hole.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_check as C

def main():
    fails = []
    # 1. A timestamp-shaped positional is NOT an acknowledgement.
    st = {}
    ok, why = C.answered_allowed('/nonexistent', 'x', st, None)
    if ok:
        fails.append('allowed with no evidence and no ack')
    # 2. The flag route still works and records the words.
    st = {}
    ok, why = C.answered_allowed('/nonexistent', 'x', st, 'keep firing the alarm')
    if not ok or 'keep firing' not in str(st.get('owner_ack')):
        fails.append('the explicit ack route failed: %s %r' % (why, st))
    # 3. Empty is refused.
    ok, _ = C.answered_allowed('/nonexistent', 'x', {}, '')
    if ok:
        fails.append('empty ack accepted')
    # 4. wd.sh must pass the flag through, and ONLY the flag.
    sh = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wd.sh')).read()
    line = [l for l in sh.splitlines() if l.strip().startswith('answered)')]
    if not line:
        fails.append('no answered verb in wd.sh')
    elif '--owner-ack' not in line[0]:
        fails.append('wd.sh does not wire the --owner-ack flag: %s' % line[0].strip())
    for f in fails:
        print('FAIL:', f)
    print('SELFTEST', 'FAILED' if fails else 'PASS', '(4 controls)')
    return 1 if fails else 0

if __name__ == '__main__':
    raise SystemExit(main())
