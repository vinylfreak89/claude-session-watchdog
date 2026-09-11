#!/usr/bin/env python3
"""Closing an item must ARCHIVE it, never destroy it.

`resolved` used to `pop` the question and drop it. D13's words survived only because they had been
copied into the digest by hand; SKEW-DETECTOR and CONTRACT-STALE-STORE had to be reconstructed from
the transcript. An item's own text is the evidence that it was real and what it asked -- a closed
item with no record is indistinguishable from one that never existed.
"""
import json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

def main():
    fails = []
    tmp = tempfile.mkdtemp()
    st = {"open_questions": {"ITEM-A": {"asked_ts": "2026-09-11T00:00:00Z", "asked_ct": 1,
                                        "text": "the words that must survive", "resends": 3}}}
    sd = os.path.join(tmp, 'state'); os.makedirs(sd, exist_ok=True)
    open(os.path.join(sd, 'state.json'), 'w').write(json.dumps(st))
    # TWO WAYS THIS CONTROL FAILED TO REACH THE CODE, both reported as the defect it hunts:
    # it first passed WD_STATE_DIR, which the tool ignores, and then put the global flags AFTER the
    # subcommand, which argparse rejects with exit 2. Both times it printed "the item was DESTROYED"
    # against a correct implementation. A control that does not reach the function is a claim about
    # it, and here its failure was indistinguishable from a true positive -- so the invocation is
    # asserted below rather than assumed.
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'wd_check.py'),
                        '--target', 'local_458e8497-0d3a-42ab-8258-f42842660d02',
                        '--state-dir', sd, 'resolved', 'ITEM-A', 'closed by measurement'],
                       capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        print('SELFTEST UNAVAILABLE: the tool did not run (rc=%d) -- %s'
              % (r.returncode, (r.stderr or '').strip()[-160:]))
        return 0
    try:
        after = json.load(open(os.path.join(sd, 'state.json')))
    except Exception as e:
        print('SELFTEST UNAVAILABLE: could not read state back (%s)' % e); return 0

    # 1. It must be gone from the OPEN set -- closing has to mean something.
    if 'ITEM-A' in (after.get('open_questions') or {}):
        fails.append('the item is still open after `resolved`')

    # 2. THE DEFECT: its words must survive somewhere.
    arch = (after.get('resolved_questions') or {}).get('ITEM-A')
    if arch is None:
        fails.append('the item was DESTROYED by `resolved`, not archived')
    else:
        if arch.get('text') != 'the words that must survive':
            fails.append('archived without its own text: %r' % arch)
        if not arch.get('resolved_ts'):
            fails.append('archived without a resolution timestamp: %r' % arch)
        # 3. The reason given on the command line is part of the record.
        if 'measurement' not in (arch.get('resolved_reason') or ''):
            fails.append('the stated reason was not recorded: %r' % arch)

    for f in fails:
        print('FAIL:', f)
    print('SELFTEST', 'FAILED' if fails else 'PASS', '(3 controls)')
    return 1 if fails else 0

if __name__ == '__main__':
    raise SystemExit(main())
