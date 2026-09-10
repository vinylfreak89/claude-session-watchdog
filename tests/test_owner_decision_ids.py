#!/usr/bin/python3
"""Control: an owner-decision id is never reused, so an add can never destroy a live decision.

The id was `'D%d' % (len(owe) + 1)`. Clear an answered decision and the next add takes an id that
is already in use and overwrites it -- silently, with no error and no diff, in the one store whose
whole purpose is not losing the owner's decisions. It happened: clearing D4 made the next add take
D6 and destroy the D6 already sitting there, and it was caught only because the GATED count
dropped by one in the listing printed straight afterwards.

Max-of-existing has the same hole from the other side (clear the highest id and it comes back), so
the control exercises BOTH sequences rather than only the one that bit.
"""
import sys, os, json, subprocess, tempfile, shutil
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, '..')

def _add(sd, text, gated=None):
    cmd = [sys.executable, os.path.join(ROOT, 'wd_wake.py'), '--state-dir', sd, '--owe-add', text]
    if gated: cmd += ['--gated-on', gated]
    subprocess.run(cmd, capture_output=True, check=True)

def _clear(sd, oid):
    subprocess.run([sys.executable, os.path.join(ROOT, 'wd_wake.py'), '--state-dir', sd, '--owe-clear', oid],
                   capture_output=True, check=True)

def _ids(sd):
    return sorted(json.load(open(os.path.join(sd, 'state.json')))['owner_decisions'].keys())

def run(name, clear_which):
    sd = tempfile.mkdtemp()
    try:
        for i in range(4): _add(sd, 'decision number %d' % (i + 1))
        before = _ids(sd)
        assert len(before) == 4, before
        _clear(sd, clear_which)
        _add(sd, 'the one that must not clobber anything')
        after = _ids(sd)
        survived = [d for d in before if d != clear_which]
        lost = [d for d in survived if d not in after]
        ok = not lost and len(after) == 4
        print('%-28s cleared %s -> ids %s : %s%s'
              % (name, clear_which, after, 'PASS' if ok else 'FAIL',
                 '' if ok else '  LOST %s' % lost))
        return 0 if ok else 1
    finally:
        shutil.rmtree(sd, ignore_errors=True)

def main():
    fails = run('clear a middle id', 'D2') + run('clear the HIGHEST id', 'D4')
    print('RESULT: %s' % ('all controls pass' if not fails else '%d FAILED' % fails))
    return 1 if fails else 0

if __name__ == '__main__':
    raise SystemExit(main())
