#!/usr/bin/python3
"""A close that MISSES must say so, in words and in its exit status.

`owe done` and `owe ungate` used to do nothing, print nothing and exit 0 when the id was not
there, so a close that missed was indistinguishable from one that worked. The reconciliation of
2026-09-09..11 measured the cost: 8 of the 13 silent no-ops in the whole window were this, and
several were the SAME id retried minutes apart -- because nothing said it had not taken.

Its siblings already fail loudly (`resolved` prints 'no open question X' and returns 1;
`queue hold` prints "no queued item 'X'"), so the decision store was the only one failing in
silence. These controls hold it to the same bar, and each one is checked to FAIL on the old
behaviour rather than merely passing on the new."""
import json, os, subprocess, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
WAKE = os.path.join(os.path.dirname(HERE), 'wd_wake.py')


def run(state_dir, *args):
    r = subprocess.run([sys.executable, WAKE, '--state-dir', state_dir] + list(args),
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def main():
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-58s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    d = tempfile.mkdtemp(prefix='owe-loud-')
    try:
        json.dump({'owner_decisions': {'D1': {'id': 'D1', 'text': 'a real one'}},
                   'owner_decision_seq': 1},
                  open(os.path.join(d, 'state.json'), 'w'))

        rc, out = run(d, '--owe-clear', 'D404')
        ck('a close that misses NAMES the id', 'no owner decision D404' in out, True)
        ck('and exits non-zero', rc != 0, True)
        ck('and changes nothing',
           list(json.load(open(os.path.join(d, 'state.json')))['owner_decisions']), ['D1'])

        rc, out = run(d, '--owe-ungate', 'D404')
        ck('an ungate that misses NAMES the id', 'no owner decision D404' in out, True)
        ck('and exits non-zero', rc != 0, True)

        # and the hit still works, silently succeeding is not the fix
        rc, out = run(d, '--owe-clear', 'D1')
        ck('a close that HITS still says so', 'D1 answered and cleared' in out, True)
        ck('and exits zero', rc, 0)
        ck('and the decision is gone',
           json.load(open(os.path.join(d, 'state.json')))['owner_decisions'], {})
        return fails
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    f = main()
    print('\nRESULT:', 'all controls pass' if not f else '%d FAILED: %s' % (len(f), ', '.join(f)))
    sys.exit(1 if f else 0)
