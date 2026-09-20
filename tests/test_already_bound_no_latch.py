"""Re-using a bound receipt for another turn is refused, and does not latch the gate.

2026-09-19: binding the Q56 delivery to a second turn was refused ("already bound to
another turn") and that refusal latched STUCK, although the delivery's state was known.
Runs against a copy of real state; the control keeps the unknown-receipt latch.
"""
import json, os, shutil, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, 'config.json')))
BASE = ['--target', CFG['target'], '--self', CFG['self'], '--row-pattern', CFG.get('row_pattern', r'[A-Z]{1,2}\d{1,3}')]


def run(st, *args):
    return subprocess.run([sys.executable, os.path.join(ROOT, 'wd_check.py')] + BASE + ['--state-dir', st, 'answered'] + list(args),
                          capture_output=True, text=True, cwd=ROOT)


def main():
    tmp = tempfile.mkdtemp()
    try:
        st = os.path.join(tmp, 'state'); shutil.copytree(os.path.join(ROOT, 'state'), st)
        path = os.path.join(st, 'state.json'); s = json.load(open(path)); s.pop('receipt_recording_failure', None)
        # A real delivery whose receipt is already bound to a turn, and an earlier turn it could answer.
        # a receipt whose id is a plain transcript uuid (an absorbed:<sha> id resolves differently)
        rid, rec = next((k, v) for k, v in sorted((s.get('send_receipts') or {}).items(), key=lambda kv: str(kv[1].get('ts')))[::-1]
                        if isinstance(v, dict) and v.get('turn_ts') and not k.startswith('absorbed:'))
        json.dump(s, open(path, 'w'))
        other = '2026-09-19T11:51:31.291Z'
        assert other != rec['turn_ts']
        p = run(st, other, '--acknowledged', rid, 'test re-use')
        assert 'already bound' in p.stdout, p.stdout + p.stderr
        assert 'receipt_recording_failure' not in json.load(open(path)), 'already-bound refusal latched the gate'
        p = run(st, other, '--acknowledged', '00000000-0000-0000-0000-000000000000', 'unknown receipt')
        assert 'receipt_recording_failure' in json.load(open(path)), 'an unknown receipt no longer latches: ' + p.stdout
        print('ok')
    finally:
        shutil.rmtree(tmp)


if __name__ == '__main__':
    main()
