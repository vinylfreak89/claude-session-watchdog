"""A malformed `answered` argument is a usage error, never a receipt failure.

2026-09-19: `wd.sh answered --help` recorded "--help" as a message id and latched the
send gate STUCK on a delivery that never existed. Runs against a copy of real state.
"""
import json, os, shutil, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, 'config.json')))
BASE = ['--target', CFG['target'], '--self', CFG['self'], '--row-pattern', CFG.get('row_pattern', '[A-Z]{1,2}\\d{1,3}')]


def main():
    tmp = tempfile.mkdtemp()
    try:
        st = os.path.join(tmp, 'state'); shutil.copytree(os.path.join(ROOT, 'state'), st)
        s = json.load(open(os.path.join(st, 'state.json'))); s.pop('receipt_recording_failure', None)
        json.dump(s, open(os.path.join(st, 'state.json'), 'w'))
        for bad in ('--help', '-h', '--evidence-typo'):
            p = subprocess.run([sys.executable, os.path.join(ROOT, 'wd_check.py')] + BASE + ['--state-dir', st, 'answered', bad],
                               capture_output=True, text=True, cwd=ROOT)
            assert 'REFUSED' in p.stdout, (bad, p.stdout, p.stderr)
            after = json.load(open(os.path.join(st, 'state.json')))
            assert 'receipt_recording_failure' not in after, 'usage error %r latched the gate' % bad
        # control: a well-formed uuid that matches no record still latches (the strict path is intact)
        p = subprocess.run([sys.executable, os.path.join(ROOT, 'wd_check.py')] + BASE + ['--state-dir', st, 'answered',
                            '00000000-0000-0000-0000-000000000000'], capture_output=True, text=True, cwd=ROOT)
        after = json.load(open(os.path.join(st, 'state.json')))
        assert 'receipt_recording_failure' in after, 'a real unknown delivery no longer latches: %s' % p.stdout
        print('ok')
    finally:
        shutil.rmtree(tmp)


if __name__ == '__main__':
    main()
